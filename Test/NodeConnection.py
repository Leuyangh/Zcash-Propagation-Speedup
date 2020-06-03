import argparse
import paramiko
import sys, os, string, threading, time

#Globals
user = "ubuntu"
filepath = "C:/Users/Eric/Documents/AWS/Eric-Keypair.pem"

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

#Main function of a thread - wait for commands and execute after creating channel
def work(addr, name):
    #Paramiko connection magic
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    #Connection  building - Use the Elastic IP to connect - TODO add other nodes
    client.connect(addr, username=user, key_filename=filepath)
    #create persistent shell
    chan = client.invoke_shell()
    #Wait for commands
    NotDone = True
    while NotDone:
        cmd = waitForWork(name)
        print("Received Command: " + cmd)
        NotDone = False#processCommand(chan, cmd)
    #Done working, time to close
    print(name + "Closing")
    client.close()

#Process User Input
def getInput():
    running = True
    while running:
        UserInput = input("Enter Command: ")
        if(UserInput == "Quit"):
            running = False
            print("User thread closing, shutting down nodes")
            for n in threadNames:
                commandBuffer.append(n + "exit")
        else:
            print("Sending command to nodes")
            commandBuffer.append(UserInput)

#Create and start threads, join when done
def main():
    Nodes = ['3.101.60.215', '54.177.243.189', '3.101.68.11'] #Elastic IPs of nodes
    Threads = []
    Count = 0
    BaseName = "Node"
    for ip in Nodes:
        FullName = BaseName + str(Count) + ":"
        t = threading.Thread(target=work, args=(ip, FullName,))
        t.start()
        Threads.append(t)
        threadNames.append(FullName)
        Count+=1
    UserThread = threading.Thread(target = getInput)
    UserThread.start()
    Threads.append(UserThread)


main()

#reference garbage
""" stdin, stdout, stderr = client.exec_command(commands) #timeout not set for now
    for line in stdout:
        print(name + ': ' + line.strip('\n'))

    #Send commands iteratively
    for cmd in commands:
        chan.send(cmd)
        chan.send('\n')
        time.sleep(1)
        resp = chan.recv(9999)
        output = resp.decode('ascii').split(',')
        print (''.join(output))"""
