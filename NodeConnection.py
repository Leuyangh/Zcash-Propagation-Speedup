import argparse, datetime
import paramiko
import sys, os, string, threading, time

########################Globals
user = "ubuntu"
filepath = "C:/Users/Eric/Documents/AWS/Eric-Keypair.pem"
commandsMessage = ">Commands: -listNodes, listPeers, -flushBuffer, -usage, -addPeer <node or -all> <ip>, -remPeer <node> <ip or -all>"
usageMessage = ">Usage: <Node(s)> -c <channel message> <optional -t waittime, defaults to 1s>. Use -ALL to send to all nodes."
logfileBase = "C:/Users/Eric/Documents/Insight/Logs/logfile"
logfileOn = False
logfileName = ""
inputFileName = ""
updateFreq = 300 #rough seconds between peerlist updates
updateCounter = 0 #global counter

allNodes = ['54.151.28.66', '54.151.20.171', '54.193.222.15', '13.57.173.210'] #Elastic IPs of nodes
threads = []
threadNames = set()
threadsRunning = []
commandBuffer = []
nodePeers = {}

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

#list all nodes - TODO list only active
def listNodes():
    print(">All Nodes: ")
    for n in threadNames:
        print(f"\t Node '{n}'' ({allNodes[int(n)]})")

#list all peers known
def listPeers():
    print(f">All Nodes and peers as of {datetime.datetime.now()}: ")
    writeToLog(f">All Nodes and peers as of {datetime.datetime.now()}: ")
    for n in threadNames:
        print(f"\tNode '{n}' ({allNodes[int(n)]}) peers ({len(nodePeers[n])}):")
        writeToLog(f"\tNode '{n}' ({allNodes[int(n)]}) peers ({len(nodePeers[n])}):")
        if n in nodePeers:
            counter = 1
            for p in nodePeers[n]:
                print(f"\t\t{counter}: {p}")
                writeToLog(f"\t\t{counter}: {p}")
                counter+=1

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

#check if command targets a valid node
def validTarget(name):
    valid = False
    for n in threadNames:
        if name == n:
            valid = True
            break
    return valid

#send commands to buffer to remove all node peers
def removeAllPeers(node):
    print(f">Pushing commands to remove all peers from node {node}, {len(nodePeers[node])} found")
    for ip in nodePeers[node]:
        commandBuffer.append(f"{node} ./src/zcash-cli disconnectnode {ip}")
    return

#write to logfile
def writeToLog(input):
    print(input)
    if logfileOn:
        with open(logfileName, "a") as f:
            original_stdout = sys.stdout
            sys.stdout = f
            print(input)
            sys.stdout = original_stdout

########################Worker thread functions

#Wait for commands in buffer and remove them if its for this thread
def waitForWork(name, chan):
    global updateCounter
    while True:
        positionCounter = 0
        for cmd in commandBuffer:
            if name == cmd.split(" ")[0]:
                commandBuffer.pop(positionCounter)
                return cmd[len(name):]
            else:
                positionCounter += 1
        time.sleep(1)
        updateCounter += 1
        if updateCounter%updateFreq == 0:
            updateCounter = 0
            #print(f">Node '{name}' updating peerlist in the background")
            updatePeerListAuto(name, chan)

#update the list of known peers for this node having already gotten output from a getpeerinfo command
def updatePeerList(name, output):
    global nodePeers
    output = clean(output)
    test = output.split("\"")
    peers = []
    for i in range(0, len(test)):
        if test[i] == "addr":
            peers.append(test[i+2])
    nodePeers[name] = peers

#update the peerlist without having received a getpeerinfo command
def updatePeerListAuto(name, chan):
    #will work only after we have moved directory into the zcash dir
    chan.send("./src/zcash-cli getpeerinfo \n")
    time.sleep(1)
    resp = chan.recv(99999)
    output = resp.decode('ascii').split(',')
    updatePeerList(name, ''.join(output))
    if logfileOn:
        with open(logfileName, "a") as f:
            original_stdout = sys.stdout
            sys.stdout = f
            listPeers()
            sys.stdout = original_stdout

#Process command, return response from node
def processCommand(chan, cmd, name, addr):
    #determine if a custom waiting duration was set
    waittime = 1
    timePos = cmd.find("-t")
    if timePos != -1:
        if cmd[timePos+2:].strip().isdigit():
            waittime = int(cmd[timePos+2:].strip())
            print(f">Wait time set to {waittime}")
        cmd = cmd[:timePos]
    #special remove all peers command
    if "removeallpeerscode" in cmd:
        removeAllPeers(name)
        return
    #to convert peer number to ip address - jank i know
    if "-p" in cmd:
        pieces = cmd.split("-p")
        peerNum = pieces[-1]
        if int(peerNum) <= len(nodePeers[name]):
            peerNum = nodePeers[name][int(peerNum) - 1]
        cmd = pieces[0] + peerNum
    #send the command and receive the response
    chan.send(cmd)
    chan.send('\n')
    time.sleep(waittime)
    resp = chan.recv(99999)
    message = "\n>Node " + name + " (" + addr + ") Received:"
    message += ("\n>______________________________________________________ \n")
    output = resp.decode('ascii').split(',')
    message += (''.join(output))
    #if the command was getpeerinfo then we want to update the peerlist
    if "getpeerinfo" in cmd:
        updatePeerList(name, ''.join(output))
    #if the command to add or disconnect a node update the peer list using the no-prior output function
    if "addnode" in cmd or "disconnectnode" in cmd:
        updatePeerListAuto(name, chan)
    #record received messages to logfile
    writeToLog(message)

#Main function of a thread - wait for commands and execute after creating channel
def work(addr, name):
    #Paramiko connection magic
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    #Connection  building - Use the Elastic IP to connect - TODO add other nodes
    print(">Node '" + name + "' connecting to " + str(addr))
    client.connect(addr, username=user, key_filename=filepath)
    #create persistent shell
    chan = client.invoke_shell()
    #loop for commands
    position = int(name.split(":")[0])
    while True:
        threadsRunning[position] = 0
        cmd = waitForWork(name, chan)
        threadsRunning[position] = 1
        processCommand(chan, cmd, name, addr)
        if "exit" in cmd:
            break
    #Done working, time to close
    #update frequency timer
    global updateFreq
    base = updateFreq/len(threadNames)
    updateFreq = base * (len(threadNames) - 1)
    threadNames.remove(name)
    threadsRunning[position] = 0
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
            return "./src/zcash-cli addnode " + ip + " add"
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
    else:
        parseInput(input)
    return True

#Get User Input
def getInput():
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
                print(">User thread waiting on node threads. Sleeping...")
                time.sleep(1)
                counter += 1
            if counter >= 15:
                print(">Waited on a node long enough. Resuming")
                break
    print(">User thread exited.")

#Create and start threads
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--logfile", help="Record to logfile", action = "store_true", default = False)
    parser.add_argument("-i", "--input", help="File of IPs to bind with", action = "store", dest = "inputFile")
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
            print(f.read())
    #create and start threads
    global threads, threadNames, nodePeers
    count = 0
    for ip in allNodes:
        name = str(count)
        t = threading.Thread(target=work, args=(ip, name,))
        t.daemon = True;
        t.start()
        threads.append(t)
        threadNames.add(name)
        threadsRunning.append(1)
        nodePeers[name] = []
        count+=1
    threadNames = sorted(threadNames)
    #with x threads running, frequency should be multiplied by x or it will update at freq/x seconds
    global updateFreq
    updateFreq *= len(threads)
    #starting user thread, only non-daemon
    UserThread = threading.Thread(target = getInput)
    UserThread.start()
    threads.append(UserThread)

#run main
main()
