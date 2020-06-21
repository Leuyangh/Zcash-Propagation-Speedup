import argparse, datetime
import paramiko
import sys, os, string, threading, time
import boto3
from twilio.rest import Client

"""
###########################
Global variables
###########################
"""
### logging and ssh channel creation globals
user = "ubuntu"
filepath = "C:/Users/Eric/Documents/AWS/Eric-Keypair.pem"
commandsMessage = ">Commands: -listNodes, listPeers, -flushBuffer, -usage, -addPeer <node or -all> <ip>, -remPeer <node> <ip or -all>"
usageMessage = ">Usage: <Node(s)> -c <channel message> <optional -t waittime, defaults to 1s>. Use -ALL to send to all nodes."
logfileBase = "C:/Users/Eric/Documents/Insight/Logs/logfile"
startupCommand = "zcash/src/zcashd -daemon --outboundconnections=200 --maxconnections=500"
twilioAuthPath = "C:/Users/Eric/Documents/Insight/TwilioAuth.txt"
logfileOn = False
logfileName = ""
updateFreq = 30                                                                 ### rough seconds between peerlist updates for each thread
writeFreq = 60                                                                  ### how often the logfile is automatically written updates to
writeCounter = 0                                                                ### global counter towards writing peers to log

#texting reminder globals
lastMessage = datetime.datetime(2020, 5, 17)
textlock = False                                                                ### lock out of sending multiple messages by different threads
textenabled = False
twilioClient = None

#for automation
experimentTime = -1                                                             ### Seconds between start/stop the nodes for experimentation purposes

#node tracking globals
allNodes = []                                                                   ### IPs of nodes
syncedNodes = {}                                                                ### dictionary node->bool, int, chan for sync status, peer count, channel of this node
threadNames = set()
threadsRunning = []                                                             ### boolean array of threads currently executing vs not executing
commandBuffer = []                                                              ### commands waiting to run
nodePeers = {}                                                                  ### current map of node->peer IPs
prevPeers = {}                                                                  ### previous peerlist, for determining if change occurred
maxDuplicates = 2

"""
###########################
Utility functions
###########################
"""

def clean(input):
    """
    clean a string of brackets, newline characters, colons and turns sequential "" into a single one - For cleaning the output of a getpeerinfo call
    """
    input = input.replace("{", "")
    input = input.replace("}", "")
    input = input.replace("[", "")
    input = input.replace("]", "")
    input = input.replace("\n", "")
    input = input.replace("\r", "")
    input = input.replace(" ", "")
    input = input.replace("\"\"", "\"")
    return input

def flushBuffer():
    """
    clears buffer of any wrongly formatted and unused commands or ones sent to dead threads
    """
    commandBuffer.clear()
    print(">Buffer Flushed")

def sendALL(input):
    """
    send command to all nodes
    """
    for n in threadNames:
        commandBuffer.append(n + " " + input)

def sendOne(node, input):
    """
    target a node with a command
    """
    commandBuffer.append(node + " " + input)

def getNodeIPs(name, value):
    """
    get ip addresses of nodes through boto using filters given from cmd line
    """
    ips = []
    ec2 = boto3.resource('ec2')
    instances = ec2.instances.filter(Filters=[{'Name': name,'Values': [value]}, {'Name' : 'instance-state-name','Values' : ['running']}])
    for i in instances:
        ips.append(i.public_ip_address)
    return ips

def validTarget(node):
    """
    check if command targets a valid node
    """
    valid = False
    for n in threadNames:
        if node == n:
            valid = True
            break
    return valid

def sendText(message):
    """
    Check if it has been 5 minutes since the last text, if so send, else let us retry after a minute
    """
    global lastMessage, textlock
    if textenabled == False or textlock == True:
        return
    textlock = True
    time_delta = (datetime.datetime.now() - lastMessage)
    total_seconds = time_delta.total_seconds()
    print(f"{total_seconds} since last text")
    if int(total_seconds) >= 300:
        if sendTextSuccess(message) == True:
            lastMessage = datetime.datetime.now()
            print(f"Setting last text to {lastMessage}")
        else:
            lastMessage = datetime.datetime.now() - datetime.timedelta(seconds = 240)
            print(f"Setting last text to {lastMessage}, 4 min ago from {datetime.datetime.now()}")
    textlock = False

def sendTextSuccess(message):
    """
    actually send a text and see if it succeeded or not
    """
    try:
        twilioClient.messages.create(to="+13107795882", from_="+12029337899", body=message)
        return True
    except:
        print(f"Error sending text message reminder: {sys.exc_info()[0]}")
        return False

def writeToLog(input):
    """
    write to logfile
    """
    if logfileOn:
        f = open(logfileName, "a")
        f.write(input)
        f.close()

def addPeer(name, addr):
    """
    add a peer to a node
    """
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command(f'zcash/src/zcash-cli addnode {addr} add')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        client.close()
    except:
        error = sys.exc_info()[0]
        print(f"An error occurred: {error}, returning from addPeer")
        sendText(f"Error: {error} occurred, go check your terminal")
        raise

def addConfigPeer(name, addr):
    """
    add a peer to a node's .conf file so it'll attempt to connect next startup
    """
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command(f'echo "addnode={addr}" >> ~/.zcash/zcash.conf')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        client.close()
    except:
        error = sys.exc_info()[0]
        print(f"An error occurred: {error}, returning from addConfigPeer")
        sendText(f"Error: {error} occurred, go check your terminal")
        raise

def removeAllPeers(node):
    """
    send commands to buffer to remove all node peers - prints & runs only when called by the CLI. Warning - slow and messy
    """
    print(f">Pushing commands to remove all peers from node {node}, {len(nodePeers[node])} found")
    for ip in nodePeers[node]:
        commandBuffer.append(f"{node} ./src/zcash-cli disconnectnode {ip}")
    return

def getBlockHeight():
    """
    Get blockchain height at this time
    """
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect("54.151.28.66", username=user, key_filename=filepath)    ### IP of a static always-on, always-synced source of truth node that I use to get block height and create AMIs from
        stdin, stdout, stderr = client.exec_command(f'zcash/src/zcash-cli getblockcount')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        client.close()
        lines = ''.join(stdout.readlines())
        lines = lines.strip()
        if lines.isdigit() == True:
            return int(lines)
        else:
            return -1
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from getBlockHeight")
        sendText(f"Error: {error} occurred, go check your terminal")
        raise
        return -1

def isSynced(name):
    """
    check if this node is synced yet, and how many peers it has
    """
    try:
        syncedNodes[name] = (False, 0)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command('zcash/src/zcash-cli getconnectioncount\n')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        client.close()
        lines = ''.join(stdout.readlines())
        output = lines.split('\n')
        if len(output) >= 2 and output[-2].isdigit():                           ### second to last line, up to the second to last char which are always "\r"
            syncedNodes[name] = (True, int(output[-2]))
            return True
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from isSynced")
        sendText(f"Error: {error} occurred, go check your terminal")

def listSync():
    """
    Tell threads to report their sync status
    """
    for n in threadNames:
        commandBuffer.append(f"{n} checksync")

"""
###########################
Functions for the peer manager thread
###########################
"""

def removeDuplicates():
    """
    Remove any copies of peers that have appeared more than the specified number of times within the networrk. E.g. peer A appears as a peer of 6 nodes but we specified 5 max, so we remove peer A from 1 node
    """
    try:
        previousPeers = []
        for name, info in syncedNodes.items():
            if info[0] == True:
                peers = nodePeers[name]
                if len(peers) != info[1]:                                       ### May need an update before removal
                    updatePeerListAuto(name)
                for p in peers:
                    if p.split(":")[0] in allNodes:                             ### Don't remove peers of nodes in the network
                        continue
                    if previousPeers.count(p) >= maxDuplicates:                 ### If we have found too many copies then disconnect and ban for 15 minutes just so this doesn't get overloaded
                        writeToLog(f">Duplicate peer found: {p}. Removing from node {name}.\n")
                        client = paramiko.SSHClient()
                        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
                        stdin, stdout, stderr = client.exec_command('zcash/src/zcash-cli disconnectnode ' + p + '\n')
                        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
                            time.sleep(0.2)
                        lines = stderr.readlines()
                        if "error" in lines:
                            writeToLog(f">Error removing duplicate {p} from node {name}\n")
                            writeToLog(f"\t>Error: {lines}")
                        stdin, stdout, stderr = client.exec_command('zcash/src/zcash-cli setban ' + p + ' add 900\n')
                        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
                            time.sleep(0.2)
                        lines = stderr.readlines()
                        if "error" in lines:
                            writeToLog(f">Error banning duplicate {p} from node {name}\n")
                            writeToLog(f"\t>Error: {lines}")
                        client.close()
                    else:
                        previousPeers.append(p)
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from removeDuplicates")
        sendText("Error occurred, go check your terminal")
        raise

def createCycle():
    """
    add all peers of speedup network to each other
    """
    for n in threadNames:
        for i in range(len(allNodes)):
            if int(n) != i:
                addPeer(n, allNodes[i])

def createConfigCycle():
    """
    Add all peers of speedup network to each other in the config file
    """
    for n in threadNames:
        for i in range(len(allNodes)):
            if int(n) != i:
                addConfigPeer(n, allNodes[i])

def managePeers():
    """
    Check to see if all nodes are synced then add them to each other as peers. Then, loop through on inverval and remove excess duplicates from the network
    """
    createdCycle = False
    failCounter = 0
    while True:
        try:
            if createdCycle == False:
                allsynced = True
                for i in syncedNodes.values():
                    if i[0] != True:
                        allsynced = False
                        break
                if len(syncedNodes) != len(threadNames):
                    allsynced = False
                if allsynced == True:
                    createConfigCycle()
                    createCycle()
                    createdCycle = True
                    writeToLog(f">Created cycle between nodes at {datetime.datetime.now()}\n")
            removeDuplicates()
            time.sleep(30)
        except:
            failCounter += 1
            if failCounter > 2:
                if textenabled == True:
                    sendTextSuccess("Manage peers thread failed 3 times, shutting down")
                return

"""
###########################
Functions for the worker threads
###########################
"""

def listNodes():
    """
    List all active nodes
    """
    print(">All Nodes: ")
    for n in threadNames:
        print(f"\t Node '{n}' ({allNodes[int(n)]})")

def writePeers():
    """
    Write all peers known to the log file
    """
    try:
        global prevPeers, writeCounter
        if sorted(prevPeers.values()) == sorted(nodePeers.values()):            ### For sure the last known one was the same as this current -> dont write
            writeToLog(f">No change as of {datetime.datetime.now()} \n")
            return
        prevPeers = nodePeers.copy()
        height = getBlockHeight()
        writeToLog(f">All Nodes and peers as of {datetime.datetime.now()}, block height {height}: \n\t[")
        prev = []
        prevIP = []
        dupeCounter = 0
        totalCounter = 0
        for n in threadNames:
            writeToLog(f"\tNode '{n}' ({allNodes[int(n)]}) peers ({len(nodePeers[n])}): \n")
            writeCounter = 0                                                    ### dont want other threads running this while we're still in it so keep resetting while this function runs
            if n in nodePeers:                                                  ### Write every peer known for every node and track how many unique IPs & IP:Port pairs are known
                counter = 1
                for p in nodePeers[n]:
                    writeToLog(f"\t\t{counter}: {p}")
                    ip = p.split(":")[0]
                    if ip in allNodes:
                        writeToLog("*")
                    writeToLog("\n")
                    counter+=1
                    totalCounter+=1
                    if p in prev:
                        dupeCounter+=1
                    else:
                        prev.append(p)
                    if not ip in prevIP:
                        prevIP.append(ip)
        writeToLog(f"\t]\n\tUNIQUE peers as of {datetime.datetime.now()}, block height {height}. ({len(prev)} total): \n\t[")
        prev = sorted(prev)
        for p in prev:                                                          ### Write unique nodes
            writeToLog(f"\t\t-{p}\n")
        writeToLog(f"\t]\n>{totalCounter} total peers with {dupeCounter} duplicates and {len(prev)} uniques at height {height} ({len(prevIP)} unique IPs)\n")
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from writePeers")
        sendText("Error occurred, go check your terminal")
        raise

def listPeers():
    """
    List all peers known and call to write them to log
    """
    print(f">All Nodes and peers as of {datetime.datetime.now()}: ")
    for n in threadNames:
        print(f"\tNode '{n}' ({allNodes[int(n)]}) peers ({len(nodePeers[n])}):")
        if n in nodePeers:
            counter = 1
            for p in nodePeers[n]:
                print(f"\t\t{counter}: {p}")
                counter+=1
    writePeers()

def updatePeerList(name, output):
    """
    Update the list of known peers for this node using output from a getpeerinfo command
    """
    global nodePeers
    oldList = nodePeers
    output = clean(output)
    test = output.split("\"")
    peers = []
    for i in range(0, len(test)):
        if test[i] == "addr":
            peers.append(test[i+2])                                             ### List example after clean [..., 'addr', '', 'IP', ..., 'addr', '', 'IP']
    nodePeers[name] = peers

def updatePeerListAuto(name):
    """
    Update the peerlist without having received the output of a getpeerinfo command - Gets the output of one and then makes the call to parse it
    """
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command('zcash/src/zcash-cli getpeerinfo \n')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        client.close()
        lines = stdout.readlines()
        updatePeerList(name, ''.join(lines))                                    ### Parse the output
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from updatePeerListAuto")
        sendText("Error occurred, go check your terminal")
        raise

def getPeerAddr(cmd, flag, name):
    """
    Get peer address from a command and return the final command to be run by a node. Returns blank in case of an error so the caller knows to return
    """
    final = ""
    pieces = cmd.split(" ")
    idx = pieces.index(flag)
    if not idx < len(pieces) - 1:                                               ### No peer number specified
        print(f">Error getting peer number specified by the {flag} flag. Aborting command with code 1: " + cmd)
        return final
    peerNum = pieces[idx + 1]
    if not peerNum.isdigit():                                                   ### Peer number includes non-digits
        print(f">Error getting peer number specified by the {flag} flag. Aborting command with code 2: " + cmd)
        return final
    if flag == "-p":                                                            ### Get peer from list of known peers of nodes in the speedup network
        if int(peerNum) < len(nodePeers[name]):
            peerNum = nodePeers[name][int(peerNum)]
        else:                                                                   ### Node isn't in our list of peers
            print(f">Error getting peer number specified by the {flag} flag. Aborting command with code 3: " + cmd)
            return final
    if flag == "-p2":                                                           ### Get node from IPs of nodes in our network
        if int(peerNum) < len(allNodes):
            peerNum = allNodes[int(peerNum)]
        else:                                                                   ### Node desired is outside the length of the list
            print(f">Error getting peer number specified by the {flag} flag. Aborting command with code 4: " + cmd)
            return final
    pieces[idx + 1] = peerNum
    pieces.pop(idx)
    final = ' '.join(pieces)
    return final

def processCommand(chan, cmd, name, addr):
    """
    Process command, return response from node
    """
    waittime = 1                                                                ### determine if a custom waiting duration was set
    if " -t " in cmd:
        pieces = cmd.split(" ")
        timePos = pieces.index("-t")
        if timePos < len(pieces) - 1:
            if pieces[timePos + 1].isdigit():
                waittime = int(pieces[timePos + 1])
                pieces.pop(timePos + 1)
        pieces.pop(timePos)
        cmd = ' '.join(pieces)
    if "removeallpeerscode" in cmd:                                             ### special remove all peers command
        removeAllPeers(name)
        return
    if "checksync" in cmd:                                                      ### special check sync status command:
        synced = isSynced(name)
        message = (f"Node {name} synced: {synced} with {syncedNodes[name][1]} connections")
        print(message)
        writeToLog(message + "\n")
        updatePeerListAuto(name)
        return
    if " -p " in cmd:                                                           ### Try to convert peer number to ip address - return if it fails
        cmd = getPeerAddr(cmd, "-p", name)
        if cmd == "":
            return
    if " -p2 " in cmd:                                                          ### Need 2 to specify the right flag
        cmd = getPeerAddr(cmd, "-p2", name)
        if cmd == "":
            return
    try:
        chan.send(cmd + '\n')
        time.sleep(waittime)
        resp = chan.recv(999999)
        message = "\n>Node " + name + " (" + addr + ") Received:"
        message += ("\n>______________________________________________________ \n")
        output = resp.decode('ascii').split(',')
        message += (''.join(output))
        if "getpeerinfo" in cmd:                                                ### If the command was getpeerinfo then we want to update the peerlist
            updatePeerList(name, ''.join(output))
        if "addnode" in cmd or "disconnectnode" in cmd:
            updatePeerListAuto(name)
        print(message)
        writeToLog(message)
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from processCommand")
        sendText("Error occurred, go check your terminal")
    finally:
        return

def waitForWork(node, chan):
    """
    Wait for commands in buffer and remove them if its for this thread - Update peerlist and write it to the log on the correct frequency
    """
    global writeCounter
    updateCounter = 0
    while True:
        try:
            positionCounter = 0
            for cmd in commandBuffer:                                           ### Search command buffer for one meant for me
                if node == cmd.split(" ")[0]:
                    commandBuffer.pop(positionCounter)
                    return cmd[len(node):]
                else:
                    positionCounter += 1
            time.sleep(1)                                                       ### Update counters every second that passes
            updateCounter += 1
            writeCounter += 1
            if updateCounter >= updateFreq:                                     ### Auto update peerlist
                updateCounter = 0
                updatePeerListAuto(node)
            if writeCounter == writeFreq:
                writeCounter = 0
                writePeers()
            if writeCounter > writeFreq:                                        ### Thread sync error possible
                writeCounter = 0
        except:
            raise

def work(addr, name):
    """
    Main function of a thread - wait for commands and execute after creating channel
    """
    client = paramiko.SSHClient()                                               ### Paramiko connection magic
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(">Node '" + name + "' connecting to " + str(addr))
    client.connect(addr, username=user, key_filename=filepath)
    chan = client.invoke_shell()                                                ### Create persistent shell and move into correct directory
    chan.send("cd zcash \n")
    time.sleep(0.1)
    resp = chan.recv(999999)                                                    ### Don't use this, its garbage node diagnostics from the initial connection
    if not isSynced(name):
        print(f"\t>Node '{name}' is still syncing")
    else:
        print(f"\t>Node '{name}' is synced with {syncedNodes[name][1]} connections")
    while True:                                                                 ### loop for commands
        try:
            threadsRunning[int(name)] = 0
            cmd = waitForWork(name, chan)
            threadsRunning[int(name)] = 1
            processCommand(chan, cmd, name, addr)
            if "exit" in cmd:
                break
        except:
            print(f"Fatal error, shutting down thread {name}")
            break
    global writeFreq
    base = writeFreq/len(threadNames)                                           ### Done working, time to close but first reduce write frequency since this thread isnt contributing to the writeCounter
    writeFreq = base * (len(threadNames) - 1)
    threadNames.remove(name)
    threadsRunning[int(name)] = 0
    try:
        stdin, stdout, stderr = client.exec_command('zcash/src/zcash-cli clearbanned\n')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
    except:
        writeToLog(f">Error with node {name} clearing ban list")
    finally:
        print(">" + name + " closed.")
        client.close()

"""
###########################
Functions for the user thread to handle inputs
###########################
"""

def parseMessage(input):
    """
    Parse message looking for add or remove peer
    """
    pieces = input.split(" ")
    if "-addpeer" in pieces:
        idx = pieces.index("-addpeer")
        if idx < len(pieces) - 1:
            ip = pieces[idx + 1]
            return "./src/zcash-cli addnode " + " ".join(pieces[idx+1:]) + " add"
        else:
            print(">Error, ip not found for -addpeer")
            return "errorcode"
    if "-rempeer" in pieces:
        idx = pieces.index("-rempeer")
        if idx < len(pieces) - 1:
            ip = pieces[idx + 1]
            if ip == "-all":
                return "removeallpeerscode"
            return "./src/zcash-cli disconnectnode " + " ".join(pieces[idx+1:])
        else:
            print(">Error, ip not found for -rempeer")
            return "errorcode"
    return input

def parseInput(input):
    """
    Parse parameters of input
    """
    pieces = input.split(" ")
    messagePos = 0
    message = ""
    for p in pieces:                                                            ### find the message input
        if p == "-c" and messagePos < len(pieces) - 1:
            message = ' '.join(pieces[messagePos+1:])
            break
        else:
            messagePos += 1
    if message == "":
        print(">Error, channel message not found")
        return
    message = parseMessage(message)
    if message == "errorcode":
        return
    if pieces[0] == "-all":                                                     ### Determine receiver nodes
        sendALL(message)
    else:
        for i in range (0, messagePos):
            if validTarget(pieces[i]):
                sendOne(pieces[i], message)
            else:
                print(">Error, invalid target, skipping...")

def handleInput(input):
    """
    Handle User Input - returns true unless the user wants to exit
    """
    input = input.strip()
    input = input.lower()
    if input == "q" or input == "quit":
        print(">User thread closing, shutting down active nodes.")
        sendALL("exit")
        return False
    elif input == "-c":
        print(commandsMessage)
    elif input == "-listnodes":
        listNodes()
    elif input == "-flushbuffer":
        flushBuffer()
    elif input == "-usage":
        print(usageMessage)
    elif input == "-listpeers":
        listPeers()
    elif input == "-checksync":
        listSync()
    elif input == "-printsyncednodeinfo":
        print(syncedNodes)
    elif input == "-stopnodes":
        stopAllNodes()
    elif input == "-startnodes":
        startAllNodes()
    else:
        parseInput(input)
    return True

def getInput():
    """
    Get the user input and try to process it. Don't restart this thread until the others report they're done running
    """
    time.sleep(len(allNodes)/2)
    running = True
    print(">Type -c to see command list")
    while running:
        UserInput = input(">Enter Command: ")
        running = handleInput(UserInput)
        time.sleep(1)
        waiting = True
        counter = 0
        while waiting:
            waiting = False
            for t in threadsRunning:
                if t == 1:
                    waiting = True
            if waiting == True:
                time.sleep(1)
                counter += 1
            if counter >= 30:
                break
    print(">User thread exited.")

"""
###########################
Functions for managing experiment runs and helpers for starting/stopping nodes
###########################
"""

def stopNode(name):
    """
    Send rpc stop command to a node
    """
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command(f"zcash/src/zcash-cli stop")
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        client.close()
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from stopNode")
        raise

def startupNode(name):
    """
    Send rpc startup command to a node
    """
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command(startupCommand)
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        client.close()
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from startup")
        sendText("Error occurred, go check your terminal")
        raise

def stopAllNodes():
    """
    Turn off all nodes and make sure by sending the sync command and ensuring they all report false
    """
    try:
        tries = 0
        while True:
            success = True
            for n in threadNames:
                stopNode(n)
                isSynced(n)
            for n in syncedNodes:
                if syncedNodes[n][0] == True:
                    success = False
                    break
            tries += 1
            if success == True:
                writeToLog(f">All nodes reported disconnected as of {datetime.datetime.now()}\n")
                break
            elif tries > 3:
                writeToLog(f">Not all nodes reported disconnected as of {datetime.datetime.now()}\n")
                for i in range(0, 10):                                          ### Spam Text since its probably important
                    sendTextSuccess("Wake up, fatal error in stopping nodes, experiment may be jeopardized")
                    time.sleep(1.2)
                break
    except:
        success = sendTextSuccess("Wake up, fatal error in starting nodes, experiment may be jeopardized")
        if not success:
            writeToLog(f">Experiment jeopardized after this time")
        time.sleep(1.2)
        raise

def startAllNodes():
    """
    Turn all nodes on by broadcasting the startup command 3 times, no way to know in real-time if it succeeds since even after startup the sync takes ~5 minutes
    """
    try:
        tries = 0
        while tries < 3:
            for n in threadNames:
                startupNode(n)
            tries += 1
        return
    except:
        success = sendTextSuccess("Wake up, fatal error in starting nodes, experiment may be jeopardized")
        if not success:
            writeToLog(f">Experiment jeopardized after this time")
        time.sleep(1.2)
        raise

def silenceNode(name, results):
    """
    send the slientmode true command to a nodes, results[name] = (success, stdout, stderr). Both should be empty on success
    """
    result = [False, "N/a", "N/a"]
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command("zcash/src/zcash-cli silentmode true")
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        client.close()
        output = ''.join(stdout.readlines())
        error = ''.join(stderr.readlines())
        if output != "" or "error" in error:
            result[1] = output
            result[2] = error
        else:
            result[0] = True
    except:
        writeToLog(f"An error occurred: {sys.exc_info()[0]}, returning from silenceNode\n")
    finally:
        results[name] = result
        return

def unsilenceNode(name, results):
    """
    send the slientmode false command to a nodes, results[name] = (success, stdout, stderr). Both should be empty on success
    """
    result = [False, "N/a", "N/a"]
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command("zcash/src/zcash-cli silentmode false")
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        client.close()
        output = ''.join(stdout.readlines())
        error = ''.join(stderr.readlines())
        if output != "" or "error" in error:
            result[1] = output
            result[2] = error
        else:
            result[0] = True
        results[name] = result
    except:
        writeToLog(f"An error occurred: {sys.exc_info()[0]}, returning from unsilenceNode\n")
    finally:
        results[name] = result
        return

def silenceAllNodes():
    """
    send the slientmode true command to all nodes (NOTE: custom rpc command, not part of standard zcashd, is included in the zcashd of Pranav's observatory nodes)
    """
    try:
        thread_list = []
        results = {}                                                            ### name : (success, stdout, stderr)
        for n in threadNames:
            thread = threading.Thread(target=silenceNode, args=(n, results))
            thread_list.append(thread)
        for thread in thread_list:
            thread.start()
        for thread in thread_list:
            thread.join()
        for r in results:
            if results[r][0] == False:
                writeToLog(f"Silencing node {r} failed at height {getBlockHeight()}. Stdout: {results[r][1]}. Stderr: {results[r][2]}.\n")
                sendText(f"Silencing node {r} failed.")
    except:
        writeToLog(f"Error silencing nodes at {datetime.datetime.now()}")
        raise

def unsilenceAllNodes():
    """
    send the slientmode false command to all nodes (NOTE: custom rpc command, not part of standard zcashd, is included in the zcashd of Pranav's observatory nodes)
    """
    try:
        thread_list = []
        results = {}                                                            ### name : (success, stdout, stderr)
        for n in threadNames:
            thread = threading.Thread(target=unsilenceNode, args=(n, results))
            thread_list.append(thread)
        for thread in thread_list:
            thread.start()
        for thread in thread_list:
            thread.join()
        for r in results:
            if results[r][0] == False:
                writeToLog(f"Unsilencing node {r} failed at height {getBlockHeight()}. Stdout: {results[r][1]}. Stderr: {results[r][2]}.\n")
                sendText(f"Unsilencing node {r} failed.")
    except:
        writeToLog(f"Error unsilencing nodes at {datetime.datetime.now()}")
        raise

def idle(experimentTime):
    """
    Wait for experiment time. Checks every 5 minutes and writes to log so we know its still running
    """
    lastflip = datetime.datetime.now()
    total_seconds = 0
    while(total_seconds < experimentTime):                                      ### Only want to check every 5 minutes since shorter than that isn't a good experiment anyway
        time.sleep(300)
        time_delta = (datetime.datetime.now() - lastflip)
        total_seconds = round(time_delta.total_seconds())
        writeToLog(f">{total_seconds} seconds since last state change, {experimentTime - total_seconds} to go until next\n")
    return

def templog(message, file):
    f = open(file, "a")
    f.write(message)
    f.close()

def manageRun():
    """
    Turn on all nodes and let them run for the time specified in the -e flag. Afterwards, turn off nodes for that period of time. Repeat. Used for gathering data about network propagation with/without speedup nodes on
    """
    writeToLog(f"Experiment time set to {experimentTime} blocks\n")
    try:
        startAllNodes()
        iteration = 1
        prevBlock = getBlockHeight()
        writeToLog(f"Initial block - {prevBlock}\n")
        prevTime = datetime.datetime.now()
        tempfileOn = logfileName[:-4] + "-ON.txt"
        tempfileOff = logfileName[:-4] + "-Off.txt"
        open(tempfileOn, "x")
        open(tempfileOff, "x")
        silent = None
        while True:                                                             ### Do forever - Start/stop every N minutes
            """
            CODE FOR IF EXPERIIMENTS ARE DESIRED IN HOURS
            writeToLog(f"Sending startup command to all nodes at {datetime.datetime.now()}. Beginning iteration {iteration}. Sleeping for {experimentTime} seconds after startup\n")
            startAllNodes()
            idle(experimentTime)
            writeToLog(f"Sending shutdown command to all nodes at {datetime.datetime.now()}. Ending iteration {iteration}.\n")
            stopAllNodes()
            idle(experimentTime)
            iteration += 1
            """
            time.sleep(0.1)
            currentBlock = getBlockHeight()
            if currentBlock == prevBlock:
                continue
            elif currentBlock > prevBlock:
                currTime = datetime.datetime.now()
                time_delta = (currTime - prevTime)
                prevTime = currTime
                writeToLog(f">New block {currentBlock} at {currTime}. {time_delta.total_seconds()} seconds after previous\n")
                if silent == None:
                    bs = """do nothing here, data isn't safe/usable"""
                elif silent == True:
                    templog(f"{currentBlock} after: {time_delta.total_seconds()} seconds\n", tempfileOff)
                else:
                    templog(f"{currentBlock} after: {time_delta.total_seconds()} seconds\n", tempfileOn)
                if currentBlock % experimentTime == 0:                          ### TBD - determine how often to turn on/off propagation for optimal research
                    writeToLog(f">Silencing nodes on block {currentBlock}\n")
                    silenceAllNodes()                                           ### zcash-cli silentmode false
                    silent = True
                elif currentBlock % experimentTime == experimentTime/2:
                    writeToLog(f">Un-silencing nodes on block {currentBlock}\n")
                    unsilenceAllNodes()                                         ### zcash-cli silentmode true
                    silent = False
            else:
                writeToLog(f"Major issue with getting block height at {datetime.datetime.now()}. Reported {currentBlock} with previous as {prevBlock}\n")
            prevBlock = currentBlock
    except:
        print(f"Exception: {sys.exc_info()} occurred at {datetime.datetime.now()}, experiment thread is shutting down\n")
        writeToLog(f"Exception: {sys.exc_info()[0]} occurred at {datetime.datetime.now()}, experiment thread is shutting down\n")
        success = sendTextSuccess(f"Exception occurred at {datetime.datetime.now()}, experiment thread is shutting down\n")
    finally:
        writeToLog(f"Experiment thread shut down at {datetime.datetime.now()}\n")
        return

def main():
    """
    Parse arguments and start all the threads
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--logfile", help="Record to logfile", action = "store_true", default = False)
    parser.add_argument("-i", "--input", help="File of IPs to bind with", action = "store", dest = "inputFile")
    parser.add_argument("-fn", "--namefilter", help="Name (key) to filter EC2 instances by", action = "store", default = "tag-key", dest = "namefilter")
    parser.add_argument("-fv", "--valuefilter", help="Value to filter EC2 instances by", action = "store", default = "ZcashNode", dest = "valuefilter")
    parser.add_argument("-t", "--text", help="get text notifications if exceptions are thrown", action = "store_true", default = False)
    parser.add_argument("-d", "--duplicates", help="max duplicates nodes in the network as peers of speedup nodes", action = "store", default = 2, dest = "maxDupe", type = int)
    parser.add_argument("-e", "--experimentTime", help="How often to start/stop nodes in minutes", action = "store", default = -1, dest = "experimentTime", type = int)
    args = parser.parse_args()
    global threadNames, nodePeers, writeFreq, allNodes, twilioClient, lastMessage, logfileOn, logfileName, textenabled, maxDuplicates, experimentTime
    allNodes = getNodeIPs(args.namefilter, args.valuefilter)                     ### get ips of instances and start thread channels
    if len(allNodes) == 0:
        print("No IPs found, that the tags arguments create the proper filter and that your nodes are running")
        return
    if args.logfile:                                                            ### turn on logfile and set its name
        logfileOn = True
        time = datetime.datetime.now()
        logfileName = f'{logfileBase}-{time:%Y-%m-%d-%H%M%S}.txt'
        print("Saving logfile under " + logfileName)
        open(logfileName, "x")
    if args.inputFile:                                                          ### Input file - list of IPs of known peers which is parsed and added to the ~/.zcash/zcash.cof file in the nodes
        try:
            with open(args.inputFile, "r") as f:
                addrs = f.readlines()                                           ### build temp channels for bulk adding to config file
                message = ""
                for a in addrs:                                                 ### create one long string and add it to their config files
                    ip = a.strip()
                    message += f"addnode={ip}\n"
                for n in allNodes:                                              ### echo to all config files
                    print(f"Adding addresses to node at {n}")
                    client = paramiko.SSHClient()
                    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    client.connect(n, username=user, key_filename=filepath)
                    stdin, stdout, stderr = client.exec_command(f'echo "{message}" >> ~/.zcash/zcash.conf')
                    client.close()
        except:
            print(f"Something went wrong adding nodes from input text to config files. Error: {sys.exc_info()[0]}. Continuing execution")
    if args.text:                                                               ### text updates if an exception is thrown
        textenabled = True
        aFile = open(twilioAuthPath, 'r')
        tAuth = aFile.readlines()
        aFile.close()
        twilioClient = Client(tAuth[0], tAuth[1])
        sendText(f"Starting up node monitor at {datetime.datetime.now()}")
    else:
        print("Text reminders disabled this run")
    maxDuplicates = args.maxDupe                                                ### max duplicates allowed
    experimentTime = args.experimentTime                                        ### Set blocks between shutdown/startup
    print(f"{maxDuplicates} maximum duplicates of a peer allowed")
    for i in range(0, len(allNodes)):     ### create and start threads
        name = str(i)
        t = threading.Thread(target=work, args=(allNodes[i], name,))
        t.setDaemon(True)
        t.start()
        threadNames.add(name)
        threadsRunning.append(t)
        nodePeers[name] = []
    threadNames = sorted(threadNames, key = int)
    writeFreq *= len(threadNames)                                               ### or else it progresses at N times too fast
    UserThread = threading.Thread(target = getInput)                            ### starting user thread, only non-daemon
    UserThread.start()
    peerThread = threading.Thread(target = managePeers)                         ### Peerlist manipulator thread
    peerThread.setDaemon(True)
    peerThread.start()
    if experimentTime > 0:                                                      ### Experiment management thread
        experimentThread = threading.Thread(target = manageRun)
        experimentThread.setDaemon(True)
        experimentThread.start()

main()                                                                          ### run Main
