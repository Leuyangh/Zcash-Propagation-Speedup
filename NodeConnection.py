import argparse, datetime
import paramiko
import sys, os, string, threading, time
import boto3
from twilio.rest import Client

########################Globals
#logging and ssh channel creation globals
user = "ubuntu"
filepath = "C:/Users/Eric/Documents/AWS/Eric-Keypair.pem"
commandsMessage = ">Commands: -listNodes, listPeers, -flushBuffer, -usage, -addPeer <node or -all> <ip>, -remPeer <node> <ip or -all>"
usageMessage = ">Usage: <Node(s)> -c <channel message> <optional -t waittime, defaults to 1s>. Use -ALL to send to all nodes."
logfileBase = "C:/Users/Eric/Documents/Insight/Logs/logfile"
startupCommand = "zcash/src/zcashd -daemon --outboundconnections=200"
twilioAuthPath = "C:/Users/Eric/Documents/Insight/TwilioAuth.txt"
logfileOn = False
logfileName = ""
inputFileName = ""
updateFreq = 10 #rough seconds between peerlist updates for each thread
writeFreq = 30 #how often the logfile is automatically written updates to
writeCounter = 0 #global counter towards writing peers to log

#texting reminder globals
lastMessage = datetime.datetime(2020, 5, 17)
textlock = False #lock out of sending multiple messages by different threads
textenabled = False
twilioClient = None

#node tracking globals
allNodes = [] # IPs of nodes
syncedNodes = {} #dictionary node->bool, int, chan for sync status, peer count, channel of this node
threadNames = set()
threadsRunning = [] #boolean array of threads currently executing vs not executing
commandBuffer = [] #commands waiting to run
nodePeers = {} #current map of node->peer IPs
prevPeers = {} #previous peerlist, for determining if change occurred
maxDuplicates = 2


########################utility functions

#clean a string of brackets, newline characters, colons and turns sequential "" into a single one
def clean(input):
    input = input.replace("{", "")
    input = input.replace("}", "")
    input = input.replace("[", "")
    input = input.replace("]", "")
    input = input.replace("\n", "")
    input = input.replace("\r", "")
    input = input.replace(" ", "")
    input = input.replace("\"\"", "\"")
    return input

#clears buffer of any wrongly formatted and unused commands or ones sent to dead threads
def flushBuffer():
    commandBuffer.clear()
    print(">Buffer Flushed")

#send command to all nodes
def sendALL(input):
    for n in threadNames:
        commandBuffer.append(n + " " + input)

#target a node
def sendOne(node, input):
    commandBuffer.append(node + " " + input)

#get ip addresses of nodes through boto
def getNodeIPs(name, value):
    ips = []
    ec2 = boto3.resource('ec2')
    instances = ec2.instances.filter(Filters=[{'Name': name,'Values': [value]}, {'Name' : 'instance-state-name','Values' : ['running']}])
    for i in instances:
        ips.append(i.public_ip_address)
    return ips

#check if command targets a valid node
def validTarget(node):
    valid = False
    for n in threadNames:
        if node == n:
            valid = True
            break
    return valid

#attempt to send a text but first go through checks
def sendText(message):
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

#actually send a text and see if it succeeded or not
def sendTextSuccess(message):
    try:
        twilioClient.messages.create(to="+13107795882", from_="+12029337899", body=message)
        return True
    except:
        print(f"Error sending text message reminder: {sys.exc_info()[0]}")
        return False

#write to logfile
def writeToLog(input):
    if logfileOn:
        f = open(logfileName, "a")
        f.write(input)
        f.close()

###################RPC call functions and Error checking
#add a peer to a node
def addPeer(name, addr):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command(f'zcash/src/zcash-cli addnode {addr} add')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
    except:
        error = sys.exc_info()[0]
        print(f"An error occurred: {error}, returning from addPeer")
        sendText(f"Error: {error} occurred, go check your terminal")
        raise

def addConfigPeer(name, addr):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command(f'echo "addnode={addr}" >> ~/.zcash/zcash.conf')
    except:
        error = sys.exc_info()[0]
        print(f"An error occurred: {error}, returning from addConfigPeer")
        sendText(f"Error: {error} occurred, go check your terminal")
        raise

#send commands to buffer to remove all node peers - prints & runs only when called by the CLI
def removeAllPeers(node):
    print(f">Pushing commands to remove all peers from node {node}, {len(nodePeers[node])} found")
    for ip in nodePeers[node]:
        commandBuffer.append(f"{node} ./src/zcash-cli disconnectnode {ip}") #TODO - make this not use the channel
    return

#get blockchain height at this time
def getBlockHeight():
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(threadNames[0])], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command(f'zcash/src/zcash-cli getblockcount')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        lines = ''.join(stdout.readlines())
        lines = lines.strip()
        if lines.isdigit() == True:
            return lines
        else:
            return "Error getting blockheight"
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from getBlockHeight")
        sendText(f"Error: {error} occurred, go check your terminal")
        raise

#check if this node is synced yet, and how many peers it has
def isSynced(name):
    try:
        syncedNodes[name] = (False, 0)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command('zcash/src/zcash-cli getconnectioncount\n')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        lines = ''.join(stdout.readlines())
        output = lines.split('\n')
        if len(output) >= 2 and output[-2].isdigit(): #second to last line, up to the second to last char which are always "\r"
            syncedNodes[name] = (True, int(output[-2]))
            return True
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from isSynced")
        sendText(f"Error: {error} occurred, go check your terminal")
    #try to fix this for nexxt time by sending a startup sync command
    try:
        writeToLog(f">Error when checking sync, sending startup command\n")
        startup(name)
    except:
        writeToLog(f">Error when sending startup command\n")
    finally:
        return False

#Tell threads to report their sync status
def listSync():
    for n in threadNames:
        commandBuffer.append(f"{n} checksync")

#remove duplicate peers
def removeDuplicates():
    try:
        writeToLog("Starting new duplicate removal round\n")
        previousPeers = []
        for name, info in syncedNodes.items():
            if info[0] == True:
                peers = nodePeers[name]
                if len(peers) != info[1]:
                    #writeToLog(f">Missed sync here with node {name}. Nodepeers finds {len(nodePeers[name])}, synced node info finds {info[1]}\n")
                    updatePeerListAuto(name)
                for p in peers:
                    if p.split(":")[0] in allNodes:
                        #writeToLog(f">{p} is one of our nodes, skipping disconnect\n")
                        continue
                    if previousPeers.count(p) >= maxDuplicates:
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

#add all peers of speedup network to each other
def createCycle():
    for n in threadNames:
        for i in range(len(allNodes)):
            if int(n) != i:
                addPeer(n, allNodes[i])

#add all peers of speedup network to each other in the config file
def createConfigCycle():
    for n in threadNames:
        for i in range(len(allNodes)):
            if int(n) != i:
                addConfigPeer(n, allNodes[i])

#Send startup command to a node that may have shut down for some reason
def startup(name):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command(startupCommand)
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from startup")
        sendText("Error occurred, go check your terminal")
        raise

#Check to see if all nodes are synced then add them to each other as peers. Then, loop through on inverval and remove excess duplicates from the network
def managePeers():
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
                if len(syncedNodes) != len(allNodes):
                    allsynced = False
                if allsynced == True:
                    createConfigCycle()
                    createCycle()
                    createdCycle = True
                    writeToLog(f">Created cycle between nodes at {datetime.datetime.now()}")
            removeDuplicates()
            time.sleep(30)
        except:
            failCounter += 1
            if failCounter > 2:
                if textenabled == True:
                    sendTextInstant("Manage peers thread failed 3 times, shutting down")
                return

########################Worker thread functions

#list all nodes - TODO list only active
def listNodes():
    print(">All Nodes: ")
    for n in threadNames:
        print(f"\t Node '{n}' ({allNodes[int(n)]})")

#write all peers known
def writePeers():
    try:
        global prevPeers, writeCounter
        if sorted(prevPeers.values()) == sorted(nodePeers.values()):
            writeToLog(f">No change as of {datetime.datetime.now()} \n")
            return
        prevPeers = nodePeers.copy()
        height = getBlockHeight()
        writeToLog(f">All Nodes and peers as of {datetime.datetime.now()}, block height {height}: \n")
        prev = []
        dupeCounter = 0
        totalCounter = 0
        for n in threadNames:
            writeToLog(f"\tNode '{n}' ({allNodes[int(n)]}) peers ({len(nodePeers[n])}): \n")
            writeCounter = 0 #dont want other threads running this while we're still in it so keep resetting while this function runs
            if n in nodePeers:
                counter = 1
                for p in nodePeers[n]:
                    writeToLog(f"\t\t{counter}: {p}")
                    if p.split(":")[0] in allNodes:
                        writeToLog("*")
                    writeToLog("\n")
                    counter+=1
                    totalCounter+=1
                    if p in prev:
                        dupeCounter+=1
                    else:
                        prev.append(p)
        writeToLog(f"\tUNIQUE peers as of {datetime.datetime.now()}, block height {height}. ({len(prev)} total): \n")
        for p in prev:
            writeToLog(f"\t\t-{p}\n")
        writeToLog(f">{totalCounter} total Nodes with {dupeCounter} duplicates and {len(prev)} uniques at height {height}\n")
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from writePeers")
        sendText("Error occurred, go check your terminal")
        return

#list all peers known
def listPeers():
    print(f">All Nodes and peers as of {datetime.datetime.now()}: ")
    for n in threadNames:
        print(f"\tNode '{n}' ({allNodes[int(n)]}) peers ({len(nodePeers[n])}):")
        if n in nodePeers:
            counter = 1
            for p in nodePeers[n]:
                print(f"\t\t{counter}: {p}")
                counter+=1
    writePeers()

#update the list of known peers for this node having already gotten output from a getpeerinfo command
def updatePeerList(name, output):
    global nodePeers
    oldList = nodePeers
    output = clean(output)
    test = output.split("\"")
    peers = []
    for i in range(0, len(test)):
        if test[i] == "addr":
            peers.append(test[i+2])
    nodePeers[name] = peers

#update the peerlist without having received a getpeerinfo command
def updatePeerListAuto(name):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(allNodes[int(name)], username=user, key_filename=filepath)
        stdin, stdout, stderr = client.exec_command('zcash/src/zcash-cli getpeerinfo \n')
        while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
            time.sleep(0.2)
        lines = stdout.readlines()
        updatePeerList(name, ''.join(lines))
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from updatePeerListAuto")
        sendText("Error occurred, go check your terminal")
        return

#get peer address from a cmd and return the final command to be run by a node
def getPeerAddr(cmd, flag, name):
    final = ""
    pieces = cmd.split(" ")
    idx = pieces.index(flag)
    #no peer number specified
    if not idx < len(pieces) - 1:
        print(f">Error getting peer number specified by the {flag} flag. Aborting command with code 1: " + cmd)
        return final
    peerNum = pieces[idx + 1]
    #peer number includes non-digits
    if not peerNum.isdigit():
        print(f">Error getting peer number specified by the {flag} flag. Aborting command with code 2: " + cmd)
        return final
    #get peer in or outside speedup network
    if flag == "-p":
        if int(peerNum) < len(nodePeers[name]):
            peerNum = nodePeers[name][int(peerNum)]
        else:
            print(f">Error getting peer number specified by the {flag} flag. Aborting command with code 3: " + cmd)
            return final
    if flag == "-p2":
        if int(peerNum) < len(allNodes):
            peerNum = allNodes[int(peerNum)]
        else:
            print(f">Error getting peer number specified by the {flag} flag. Aborting command with code 4: " + cmd)
            return final
    pieces[idx + 1] = peerNum
    pieces.pop(idx)
    final = ' '.join(pieces)
    return final

#Process command, return response from node
def processCommand(chan, cmd, name, addr):
    #determine if a custom waiting duration was set
    waittime = 1
    if " -t " in cmd:
        pieces = cmd.split(" ")
        timePos = pieces.index("-t")
        if timePos < len(pieces) - 1:
            if pieces[timePos + 1].isdigit():
                waittime = int(pieces[timePos + 1])
                pieces.pop(timePos + 1)
        pieces.pop(timePos)
        cmd = ' '.join(pieces)
    #special remove all peers command
    if "removeallpeerscode" in cmd:
        removeAllPeers(name)
        return
    #special check sync status command:
    if "checksync" in cmd:
        synced = isSynced(name)
        message = (f"Node {name} synced: {synced} with {syncedNodes[name][1]} connections")
        print(message)
        writeToLog(message + "\n")
        updatePeerListAuto(name)
        return
    #to convert peer number to ip address - jank i know. p converts peer outside network, p2 connects peer in speedup network
    if " -p " in cmd:
        cmd = getPeerAddr(cmd, "-p", name)
        if cmd == "":
            return
    if " -p2 " in cmd:
        cmd = getPeerAddr(cmd, "-p2", name)
        if cmd == "":
            return
    #send the command and receive the response
    try:
        chan.send(cmd + '\n')
        time.sleep(waittime)
        resp = chan.recv(999999)
        message = "\n>Node " + name + " (" + addr + ") Received:"
        message += ("\n>______________________________________________________ \n")
        output = resp.decode('ascii').split(',')
        message += (''.join(output))
        #if the command was getpeerinfo then we want to update the peerlist
        if "getpeerinfo" in cmd:
            updatePeerList(name, ''.join(output))
        #if the command to add or disconnect a node update the peer list using the no-prior output function
        if "addnode" in cmd or "disconnectnode" in cmd:
            updatePeerListAuto(name)
        #record received messages to logfile
        print(message)
        writeToLog(message)
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from processCommand")
        sendText("Error occurred, go check your terminal")
    finally:
        return

#Wait for commands in buffer and remove them if its for this thread
def waitForWork(node, chan):
    global writeCounter
    updateCounter = 0
    while True:
        try:
            positionCounter = 0
            #search command buffer for one meant for me
            for cmd in commandBuffer:
                if node == cmd.split(" ")[0]:
                    commandBuffer.pop(positionCounter)
                    return cmd[len(node):]
                else:
                    positionCounter += 1
            time.sleep(1)
            updateCounter += 1
            writeCounter += 1
            #auto update peerlist
            if updateCounter >= updateFreq:
                #print(f"Node {name} updating peerlist")
                updateCounter = 0
                updatePeerListAuto(node)
            if writeCounter == writeFreq:
                writeCounter = 0
                writePeers()
            if writeCounter > writeFreq: #thread sync error possible
                writeCounter = 0
        except:
            raise

#Main function of a thread - wait for commands and execute after creating channel
def work(addr, name):
    #Paramiko connection magic
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    #Connection  building - Use the Elastic IP to connect - TODO add other nodes
    print(">Node '" + name + "' connecting to " + str(addr))
    client.connect(addr, username=user, key_filename=filepath)
    #create persistent shell and move into correct directory, start up zcash
    chan = client.invoke_shell()
    chan.send("cd zcash \n")
    time.sleep(0.1)
    resp = chan.recv(999999) #don't use this, its garbage from node startup
    chan.send(startupCommand + " \n")
    time.sleep(0.2)
    resp = chan.recv(999999)
    if not isSynced(name):
        print(f"\t>Node '{name}' is still syncing")
    else:
        print(f"\t>Node '{name}' is synced with {syncedNodes[name][1]} connections")
    #loop for commands
    while True:
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
    #Done working, time to close but first reduce write frequency since this thread isnt contributing to the writeCounter
    global writeFreq
    base = writeFreq/len(threadNames)
    writeFreq = base * (len(threadNames) - 1)
    threadNames.remove(name)
    threadsRunning[int(name)] = 0
    stdin, stdout, stderr = client.exec_command('zcash/src/zcash-cli clearbanned\n')
    while not stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
        time.sleep(0.2)
    print(">" + name + " closed.")
    client.close()

########################User thread functions

#Parse message looking for add or remove peer
def parseMessage(input):
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

#Parse parameters of input
def parseInput(input):
    pieces = input.split(" ")
    messagePos = 0
    message = ""
    #find the message input
    for p in pieces:
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
    #determine receiver nodes
    if pieces[0] == "-all":
        sendALL(message)
    else:
        for i in range (0, messagePos):
            if validTarget(pieces[i]):
                sendOne(pieces[i], message)
            else:
                print(">Error, invalid target, skipping...")

#Handle User Input
def handleInput(input):
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
    else:
        parseInput(input)
    return True

#Get User Input
def getInput():
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
                #print(">User thread waiting on node threads. Sleeping...")
                time.sleep(1)
                counter += 1
            if counter >= 30:
                break
    print(">User thread exited.")

#Create and start threads
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--logfile", help="Record to logfile", action = "store_true", default = False)
    parser.add_argument("-i", "--input", help="File of IPs to bind with", action = "store", dest = "inputFile")
    parser.add_argument("-fn", "--namefilter", help="Name (key) to filter EC2 instances by", action = "store", default = "tag-key", dest = "namefilter")
    parser.add_argument("-fv", "--valuefilter", help="Value to filter EC2 instances by", action = "store", default = "ZcashNode", dest = "valuefilter")
    parser.add_argument("-t", "--text", help="get text notifications if exceptions are thrown", action = "store_true", default = False)
    parser.add_argument("-d", "--duplicates", help="max duplicates nodes in the network as peers of speedup nodes", action = "store", default = 2, dest = "maxDupe")
    args = parser.parse_args()
    #create and start threads
    global threadNames, nodePeers, writeFreq, allNodes, twilioClient, lastMessage, logfileOn, logfileName, inputFileName, textenabled, maxDuplicates
    #get ips of instances and start thread channels
    allNodes = getNodeIPs(args.namefilter, args.valuefilter)
    if len(allNodes) == 0:
        print("No IPs found, that the tags arguments create the proper filter and that your nodes are running")
        return
    #turn on logfile and set its name
    if args.logfile:
        logfileOn = True
        time = datetime.datetime.now()
        logfileName = f'{logfileBase}-{time:%Y-%m-%d-%H%M%S}.txt'
        print("Saving logfile under " + logfileName)
        open(logfileName, "x")
    #check input file and add all IPs in it to the nodes config files
    if args.inputFile:
        inputFileName = args.inputFile
        print(f'Input file: {inputFileName}')
        try:
            with open(inputFileName, "r") as f:
                addrs = f.readlines()
                #build temp channels for bulk adding to config file
                for n in allNodes:
                    print(f"Adding addresses to node at {n}")
                    client = paramiko.SSHClient()
                    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    client.connect(n, username=user, key_filename=filepath)
                    #create one long string and add it to their config files
                    message = ""
                    for a in addrs:
                        ip = a.strip()
                        message += f"addnode={ip}\n"
                    stdin, stdout, stderr = client.exec_command(f'echo "{message}" >> ~/.zcash/zcash.conf')
                    client.close()
        except:
            print(f"Something went wrong adding nodes from input text to config files. Error: {sys.exc_info()[0]}")
    #text updates if an exception is thrown
    if args.text:
        textenabled = True
        aFile = open(twilioAuthPath, 'r')
        tAuth = aFile.readlines()
        aFile.close()
        twilioClient = Client(tAuth[0], tAuth[1])
        sendText(f"Starting up node monitor at {datetime.datetime.now()}")
    else:
        print("Text reminders disabled this run")
    #max duplicates allowed
    maxDuplicates = int(args.maxDupe)
    print(f"{maxDuplicates} maximum duplicates of a peer allowed")
    count = 0
    for ip in allNodes:
        name = str(count)
        t = threading.Thread(target=work, args=(ip, name,))
        t.daemon = True
        t.start()
        threadNames.add(name)
        threadsRunning.append(1)
        nodePeers[name] = []
        count+=1
    threadNames = sorted(threadNames, key = int)
    writeFreq *= len(threadNames) #or else it progresses at N times too fast
    #starting user thread, only non-daemon
    UserThread = threading.Thread(target = getInput)
    UserThread.start()
    #Peerlist manipulator thread
    peerThread = threading.Thread(target = managePeers)
    peerThread.daemon = True
    peerThread.start()

#run main
main()
