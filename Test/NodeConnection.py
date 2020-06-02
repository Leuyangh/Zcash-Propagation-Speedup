import base64
import paramiko
import sys, os, string, threading, time

#Globals
user = "ubuntu"
filepath = "C:/Users/Eric/Documents/AWS/Eric-Keypair.pem"
commands = ["sudo apt-get update && sudo apt-get upgrade", "y"]
testCommand = "mkdir Test \n ls"

#Functions - Thread work + Main for now - TODO: Monitoring + updating peer list
def work(addr, name):
    #Paramiko connection magic
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    #Connection  building - Use the Elastic IP to connect - TODO add other nodes
    client.connect(addr, username=user, key_filename=filepath)
    #create persistent shell
    chan = client.invoke_shell()
    #Send commands - TODO Make a loop or something? monitoring
    """stdin, stdout, stderr = client.exec_command(commands) #timeout not set for now
    for line in stdout:
        print(name + ': ' + line.strip('\n'))"""

    #Send commands iteratively
    for cmd in commands:
        chan.send(cmd)
        chan.send('\n')
        time.sleep(1)
        resp = chan.recv(9999)
        output = resp.decode('ascii').split(',')
        print (''.join(output))


    client.close()

#Create and start threads, join when done
def main():
    Nodes = ['3.101.60.215', '54.177.243.189', '3.101.68.11'] #Elastic IPs of nodes
    Threads = []
    BaseName = 'TestNode'
    Count = 0
    for ip in Nodes:
        FullName = BaseName + str(Count)
        t = threading.Thread(target=work, args=(ip, FullName,))
        t.start()
        Threads.append(t)
        Count+=1
    for t in Threads:
        t.join()

main()
