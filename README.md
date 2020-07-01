# **Zcash block propagation speedup network**
Full slides with images [here](https://docs.google.com/presentation/d/1QnESRBPQVNrn7NXbAMT1VdAKPy0QDQcwCxwDPirhYdk/edit#slide=id.g89296bf9e4_3_105).
## Introduction
The block propagation speedup network is an idea to reduce propagation time on decentralized networks which, by their nature, lead to non-optimal network topologies. Because there is no central authority, connections between peers are ad-hoc and can lead to situations where a large number of hops is required to propagate a message to all other peers. With financial infrastructure, low latency is always better.
## Solution
The speedup network is created via a modified Terraform script which creates a specified count of Ec2 instances synced to the Zcash network but running a modified daemon which allows for custom outgoing connection counts (instead of the standard max of 20). The base repo can be found [here](https://github.com/insight-infrastructure/terraform-zcash-aws-ec2-node). Speedup nodes connect to all nodes in the organic network as well as to each other, limiting the maximum hops between any two nodes to 3 `Node->Speedup->Speedup->Node`.
NodeConnection.py monitors the speedup network and removes peers which have connected to too many speedup nodes as to not perturbe the organic network.
## Experimentation
The code supports experimentation since it is vital to the project to know if results are produced. The modified Zcash daemon supports an additional RPC command to silence/unsilence that node and the manager code automatically handles starting/stopping on a given block interval or time duration. Results are gathered using the Insight Fellowship's decentralized consensus lab observatory nodes which can be found at [this link](https://github.com/insight-decentralized-consensus-lab/). Analysis is run in the included notebooks.
## Results
See slides for detailed graphs. Results are gathered from experiment (on) and control (off) periods of roughly 3 hours each to account for natural variation.
