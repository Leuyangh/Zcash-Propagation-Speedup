import argparse, datetime
import sys, os, string

"""A line starting with '-' or a number is an IP address and we don't want to copy those"""
def isUseful(line):
    if line[0].isdigit() or line[0] == '-':
        return False
    if line[0] == ']' or line[0] == '[':
        return False
    return True

"""Write to the output file"""
def writeToFile(outputFile, line):
    f = open(outputFile, "a")
    f.write(line + "\n")
    f.close()

"""Remove all the IPs from a log file so that only the meta data remains"""
def cleanLog(inputFile, outputFile):
    open(outputFile, 'w')
    with open(inputFile, 'r') as f:
        for l in f:
            line = l.strip()
            if len(line) == 0:
                continue
            if isUseful(line):
                writeToFile(outputFile, line)
        f.close()
        return

"""Take command line input and call the cleaning function"""
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "-input", help="File to clean", action = "store", dest = "inputFile")
    args = parser.parse_args()
    #if the file isn't a txt file then quit, otherwise try to clean it
    if args.inputFile[-4:] != ".txt":
        print("Not a valid logfile")
        return
    else:
        outputFile = args.inputFile[:-4] + "-CLEAN.txt"
        cleanLog(args.inputFile, outputFile)
        print("Cleaned file written to: " + outputFile)

main()
