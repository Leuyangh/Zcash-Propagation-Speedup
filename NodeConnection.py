import argparse, datetime
import paramiko
import sys, os, string, threading, time
import boto3
from twilio.rest import Client

########################Globals
user = "ubuntu"
filepath = "/Users/erichuang/Downloads/Eric-Keypair.pem"
commandsMessage = ">Commands: -listNodes, listPeers, -flushBuffer, -usage, -addPeer <node or -all> <ip>, -remPeer <node> <ip or -all>"
usageMessage = ">Usage: <Node(s)> -c <channel message> <optional -t waittime, defaults to 1s>. Use -ALL to send to all nodes."
logfileBase = "/Users/erichuang/Documents/InsightLogs/"
startupCommand = "zcash/src/zcashd -daemon --outboundconnections=200"
twilioAuthPath = "/Users/erichuang/Documents/twilioAuth.txt"
logfileOn = False
logfileName = ""
inputFileName = ""
updateFreq = 10 #rough seconds between peerlist updates for each thread
writeFreq = 30 #how often the logfile is automatically written updates to
writeCounter = 0

tAuth = [] #for texting
lastMessage = datetime.datetime(2020, 5, 17)
textlock = False
allNodes = []#['54.151.28.66', '54.151.20.171', '54.193.222.15', '13.57.173.210', '54.241.71.228', '54.193.121.153', '54.183.241.216', '54.183.141.249', '54.176.230.15', '54.219.174.106'] #Elastic IPs of nodes
syncedNodes = {} #dictionary node->bool, int, chan for sync status, peer count, channel of this node
threadNames = set()
threadsRunning = [] #boolean array of threads currently executing vs not executing
commandBuffer = [] #commands waiting to run
nodePeers = {} #current map of node->peer IPs
prevPeers = {} #previous peerlist, for determining if change occurred

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
        print(f"An error occurred: {sys.exc_info()[0]}, returning from addPeer")
        sendText("Error occurred, go check your terminal")
    finally:
        return

#for silently removing peers without printing to CLI
def removePeer(name, chan, addr):
    chan.send(f"./src/zcash-cli disconnectnode {addr}")
    time.sleep(0.3)
    resp = chan.recv(999999)

#send commands to buffer to remove all node peers - prints & runs only when called by the CLI
def removeAllPeers(node):
    print(f">Pushing commands to remove all peers from node {node}, {len(nodePeers[node])} found")
    for ip in nodePeers[node]:
        commandBuffer.append(f"{node} ./src/zcash-cli disconnectnode {ip}") #TODO - make this not use the channel
    return

#RPC Error checking
""" "version": 2010253,
  "protocolversion": 170010,
  "walletversion": 60000,
  "balance": 0.00000000,
  "blocks": 863233,
  "timeoffset": 0,
  "connections": 23,
  "proxy": "",
  "difficulty": 48227803.91582687,
  "testnet": false,
  "keypoololdest": 1591040370,
  "keypoolsize": 102,
  "paytxfee": 0.00000000,
  "relayfee": 0.00000100,
  "errors": " """ #Example GETINFO call return
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
        #lines = lines.replace(',', ':')
        #lines = lines.replace("\"", "")
        #lines = clean(lines)
        #lines = lines.split(":")
        lines = lines.strip()
        if lines.isdigit() == True:
            #idx = lines.index("blocks")
            return lines
        else:
            return "Error getting blockheight"
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from getBlockHeight")
        sendText("Error occurred, go check your terminal")

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
        sendText("Error occurred, go check your terminal")
    try:
        writeToLog(f">Error when checking sync, sending startupCommand\n")
        startup(name)
    except:
        writeToLog(f">Error when checking sync, sending startupCommand2\n")
        sendText("Error occurred, go check your terminal")
    finally:
        return False

def listSync():
    for n in threadNames:
        commandBuffer.append(f"{n} checksync")

#remove duplicate peers
## TODO:
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
                    if previousPeers.count(p) > 1:
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
    finally:
        return

#add peers of my network
def createCycle():
    for n in threadNames:
        for i in range(len(allNodes)):
            if int(n) != i:
                addPeer(n, allNodes[i])

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
    finally:
        return

def managePeers():
    createdCycle = False
    while True:
        if createdCycle == False:
            allsynced = True
            for i in syncedNodes.values():
                if i[0] != True:
                    allsynced = False
                    break
            if len(syncedNodes) != len(allNodes):
                allsynced = False
            if allsynced == True:
                createCycle()
                createdCycle = True
        removeDuplicates()
        time.sleep(updateFreq)

#write to logfile
def writeToLog(input):
    if logfileOn:
        f = open(logfileName, "a")
        f.write(input)
        f.close()

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
        writeToLog(f">All Nodes and peers as of {datetime.datetime.now()}, block height {getBlockHeight()}: \n")
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
        writeToLog(f">{totalCounter} total Nodes with {dupeCounter} duplicates and {len(prev)} uniques\n")
    except:
        print(f"An error occurred: {sys.exc_info()[0]}, returning from writePeers")
        sendText("Error occurred, go check your terminal")
    finally:
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
    finally:
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
            #print("Auto writing peerlist")
            writeCounter = 0
            writePeers()
        if writeCounter > writeFreq: #thread sync error possible
            writeCounter = 0

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
        threadsRunning[int(name)] = 0
        cmd = waitForWork(name, chan)
        threadsRunning[int(name)] = 1
        processCommand(chan, cmd, name, addr)
        if "exit" in cmd:
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

def sendText(message):
    global lastMessage, textlock
    time_delta = (datetime.datetime.now() - lastMessage)
    total_seconds = time_delta.total_seconds()
    print(f"{total_seconds} since last text")
    if int(total_seconds) >= 300 and textlock == False:
        textlock = True
        success = sendTextInstant(message)
        if success == True:
            lastMessage = datetime.datetime.now()
            print(f"Setting last text to {lastMessage}")
        else:
            lastMessage = datetime.datetime.now() - datetime.timedelta(seconds = 240)
            print(f"Setting last text to {lastMessage}, 4 min ago from {datetime.datetime.now()}")
        textlock = False

def sendTextInstant(message):
    success = True
    try:
        client = Client(tAuth[0], tAuth[1])
        client.messages.create(to="+13107795882", from_="+12029337899", body=message)
    except:
        print(f"Error sending text message reminder: {sys.exc_info()[0]}")
        success = False
    finally:
        return success

#Create and start threads
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--logfile", help="Record to logfile", action = "store_true", default = False)
    parser.add_argument("-i", "--input", help="File of IPs to bind with", action = "store", dest = "inputFile")
    parser.add_argument("-fn", "--namefilter", help="Name (key) to filter EC2 instances by", action = "store", default = "tag-key", dest = "namefilter")
    parser.add_argument("-fv", "--valuefilter", help="Value to filter EC2 instances by", action = "store", default = "ZcashNode", dest = "valuefilter")
    args = parser.parse_args()
    #turn on logfile and set its name
    if args.logfile:
        global logfileOn
        logfileOn = True
        time = datetime.datetime.now()
        global logfileName
        logfileName = f'{logfileBase}-{time:%Y-%m-%d-%H%M%S}.txt'
        print("Saving logfile under " + logfileName)
        open(logfileName, "x")
    #check input file and print, also set global
    if args.inputFile:
        global inputFileName
        inputFileName = args.inputFile
        print(f'Input file: {inputFileName}')
        with open(inputFileName, "r") as f:
            print(f.read()) #TODO do smth with file
    #create and start threads
    global threadNames, nodePeers, writeFreq, allNodes, tAuth, lastMessage
    #text me updates TODO get rid of these they're annoying
    aFile = open(twilioAuthPath, 'r')
    tAuth = aFile.readlines()
    aFile.close()
    sendText(f"Starting up node monitor at {datetime.datetime.now()}")
    sendText("ERROR: SHOULD NOT BE RECEIVED")
    allNodes = getNodeIPs(args.namefilter, args.valuefilter)
    if len(allNodes) == 0:
        print("No IPs found, that the tags arguments create the proper filter and that your nodes are running")
        return
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
