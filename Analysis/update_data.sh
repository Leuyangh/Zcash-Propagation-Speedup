### copy files from servers ###
# Virginia
rsync -avL --rsh=ssh --progress -e                                  \
        "ssh -i /home/ubuntu/.ssh/VIRGINIA-KEY.pem"                \
        ubuntu@100.25.205.64:/home/ubuntu/.zcash/*.csv ./virginia/

# London
rsync -avL --rsh=ssh --progress -e                                  \
        "ssh -i /home/ubuntu/.ssh/LONDON-KEY.pem"                  \
        ubuntu@3.10.116.190:/home/ubuntu/.zcash/*.csv ./london/

# Africa
rsync -avL --rsh=ssh --progress -e                                  \
    "ssh -i /home/ubuntu/.ssh/AFRICA-KEY.pem"                      \
    ubuntu@13.244.102.241:/home/ubuntu/.zcash/*.csv ./africa/

# Mumbai
rsync -avL --rsh=ssh --progress -e                                  \
    "ssh -i /home/ubuntu/.ssh/MUMBAI-KEY.pem"                      \
    ubuntu@13.127.252.139:/home/ubuntu/.zcash/*.csv ./mumbai/

rm zipped_data.tar.gz

tar -czvf zipped_data.tar.gz *

wormhole send zipped_data.tar.gz
