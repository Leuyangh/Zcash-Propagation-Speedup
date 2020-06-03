import argparse
import paramiko
import sys, os, string, threading, time

#Globals
user = "ubuntu"
filepath = "C:/Users/Eric/Documents/AWS/Eric-Keypair.pem"
commandsMessage = ">Commands: -listNodes, -flushBuffer, -usage"
usageMessage = ">Usage: <Node><channel message>. Use -ALL to send to all nodes."

allNodes = ['3.101.60.215', '54.177.243.189', '3.101.68.11'] #Elastic IPs of nodes
threadNames = []
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
def processCommand(chan, cmd, name):
    chan.send(cmd)
    chan.send('\n')
    time.sleep(1)
    resp = chan.recv(9999)
    print("\n>" + name + " Received: \n")
    output = resp.decode('ascii').split(',')
    print (''.join(output))

#Main function of a thread - wait for commands and execute after creating channel
def work(addr, name):
    #Paramiko connection magic
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    #Connection  building - Use the Elastic IP to connect - TODO add other nodes
    client.connect(addr, username=user, key_filename=filepath)
    #create persistent shell
    chan = client.invoke_shell()
    #loop for commands
    while True:
        cmd = waitForWork(name)
        processCommand(chan, cmd, name)
        if(cmd == "exit"):
            break
    #Done working, time to close
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

#Handle User Input
def handleInput(input):
    if input == "-c":
        print(commandsMessage)
    if input == "-listNodes":
        listNodes()
    if input == "-flushBuffer":
        flushBuffer()
    if input == "-usage":
        print(usageMessage)
    else:
        if input[:4] == "-ALL":
            sendALL(input[4:])
        else:
            commandBuffer.append(input)

#Get User Input
def getInput():
    running = True
    print(">Type -c to see command list")
    while running:
        UserInput = input(">Enter Command: ")
        if(UserInput == "Quit" or UserInput == "quit" or UserInput == "q"):
            running = False
            print(">User thread closing, shutting down active nodes.")
            for n in threadNames:
                commandBuffer.append(n + "exit")
        else:
            handleInput(UserInput)
            time.sleep(2)
    print(">User thread exited.")

#Create and start threads
def main():
    Threads = []
    Count = 0
    BaseName = "Node"
    for ip in allNodes:
        FullName = BaseName + str(Count) + ":"
        t = threading.Thread(target=work, args=(ip, FullName,))
        t.start()
        Threads.append(t)
        threadNames.append(FullName)
        Count+=1
    UserThread = threading.Thread(target = getInput)
    UserThread.start()
    Threads.append(UserThread)

#run main
main()
