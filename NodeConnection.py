import argparse, datetime
import paramiko
import sys, os, string, threading, time

#Globals
user = "ubuntu"
filepath = "C:/Users/Eric/Documents/AWS/Eric-Keypair.pem"
commandsMessage = ">Commands: -listNodes, -flushBuffer, -usage, -addPeer <node or -all> <ip>, -remPeer <node> <ip or -all>"
usageMessage = ">Usage: <Node><channel message> <optional -t waittime, defaults to 1s>. Use -ALL to send to all nodes."
logfileBase = "C:/Users/Eric/Documents/Insight/Logs/logfile"
logfileOn = False
logfileName = ""
inputFileName = ""

#rpc commands
rpcadd = "./src/zcash-cli addpeer"

allNodes = ['54.151.28.66', '54.151.20.171', '54.151.20.171'] #Elastic IPs of nodes
threadNames = set()
threadsRunning = []
commandBuffer = []

#Functions - Thread work + Main for now - TODO: Monitoring + updating peer list
#Wait for commands in buffer and remove them if its for this thread
def waitForWork(name):
    while True:
        positionCounter = 0
        for cmd in commandBuffer:
            if name == cmd[:len(name)]:
                commandBuffer.pop(positionCounter)
                return cmd[len(name):]
            else:
                positionCounter += 1
        time.sleep(1)

#Process command, return response from node
def processCommand(chan, cmd, name, addr):
    chan.send(cmd)
    chan.send('\n')
    time.sleep(1)
    resp = chan.recv(9999)
    print("\n>" + name + " (" + addr + ") Received:")
    print(">______________________________________________________ \n")
    output = resp.decode('ascii').split(',')
    print (''.join(output))
    #record received messages to logfile
    if logfileOn:
        with open(logfileName, "a") as f:
            original_stdout = sys.stdout
            sys.stdout = f
            print("\n>" + name + " (" + addr + ") Received:")
            print(">______________________________________________________ \n")
            output = resp.decode('ascii').split(',')
            print (''.join(output))
            sys.stdout = original_stdout

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
        cmd = waitForWork(name)
        threadsRunning[position] = 1
        processCommand(chan, cmd, name, addr)
        if(cmd == "exit"):
            break
    #Done working, time to close
    threadNames.remove(name)
    threadsRunning[position] = 0
    print(">" + name + " closed.")
    client.close()

#list all nodes - TODO list only active
def listNodes():
    print(">All Nodes: ")
    for n in threadNames:
        print("\t"+n)

#clears buffer of any wrongly formatted and unused commands or ones sent to dead threads
def flushBuffer():
    commandBuffer.clear()
    print(">Buffer Flushed")

#send command to all nodes
def sendALL(input):
    for n in threadNames:
        commandBuffer.append(n+input)

#check if command targets a valid node
def validTarget(cmd):
    valid = False
    for n in threadNames:
        if cmd[:len(n)] == n:
            valid = True
            break
    return valid

#make an RPC call to add a peer to the specified node(s)
def addPeer(input):
    cmd, node, peer = input.split(" ")
    if node == "-all" or validTarget(node):
        asdfa
    else:
        print(">Invalid target. Node name incorrect or node has been closed. Try -listNodes to see running nodes")
#make an RPC call to remove a peer from the specified node(s)
def remPeer(input):
    cmd, node, peer = input.split(" ")
    if validTarget(node):
        asdf
    else:
        print(">Invalid target. Node name incorrect or node has been closed. Try -listNodes to see running nodes")

#Handle User Input
def handleInput(input):
    input = input.strip()
    input = input.lower()
    if(input == "quit" or input == "q"):
        print(">User thread closing, shutting down active nodes.")
        for n in threadNames:
            commandBuffer.append(n + "exit")
        return False
    elif input == "-c":
        print(commandsMessage)
    elif input == "-listnodes":
        listNodes()
    elif input == "-flushbuffer":
        flushBuffer()
    elif input == "-usage":
        print(usageMessage)
    elif input[:8] == "-addpeer":
        addPeer(input)
    elif input[:8] == "-rempeer":
        removePeer(input)
    else:
        if input[:4] == "-all":
            sendALL(input[4:])
        else:
            if validTarget(input) == True:
                commandBuffer.append(input)
            else:
                print(">Invalid target. Node name incorrect or node has been closed. Try -listNodes to see running nodes")
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
        while waiting:
            waiting = False
            for t in threadsRunning:
                if t == 1:
                    waiting = True
            if waiting == True:
                print(">User thread waiting on node threads. Sleeping...")
                time.sleep(1)
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
        logfileName = f'{logfileBase}-{time:%Y-%m-%d-%H%M}.txt'
        print("Saving logfile under " + logfileName)
        open(logfileName, "x")
    #check input file and print, also set global
    if args.inputFile != "":
        global inputFileName
        inputFileName = args.inputFile
        print(f'Input file: {inputFileName}')
        with open(inputFileName, "r") as f:
            print(f.read())
    #create and start threads
    threads = []
    count = 0
    for ip in allNodes:
        name = str(count) + ":"
        t = threading.Thread(target=work, args=(ip, name,))
        t.start()
        threads.append(t)
        threadNames.add(name)
        threadsRunning.append(1)
        count+=1
    UserThread = threading.Thread(target = getInput)
    UserThread.start()
    threads.append(UserThread)

#run main
main()
