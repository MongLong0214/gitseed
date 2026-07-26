"""Model grading — but only after the model has been shown to hold the contract.

The seed this project rebuilds checks that the configured model is *installed*
and then trusts whatever it returns. Measured on the seed's own code, a 1.5b
model called clean code malicious in 9 of 14 runs and put the description in the
reason field; the seed's default 7b did neither. The seed never tells the user
which side of that line they are on (docs/PHASE1-EVIDENCE.md D-3).
"""
