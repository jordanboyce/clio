MINI-PWR, AFW =
AFW                                              OR   AFW-BOTH-TRAINS AFW-CCF-FS
AFW-BOTH-TRAINS                                  AND  AFW-TRAIN-A AFW-TRAIN-B
AFW-TRAIN-A                                      OR   AFW-MDP-A-FS AFW-MDP-A-FR AFW-SUPPLY
AFW-TRAIN-B                                      OR   AFW-MDP-B-FS AFW-MDP-B-FR AFW-SUPPLY /HE-CROSSTIE
AFW-SUPPLY                                       TRAN
^EOS
MINI-PWR, AFW-SUPPLY =
AFW-SUPPLY                                       OR   AFW-CST-EMPTY AFW-SUCTION
AFW-SUCTION                                      2/3   AFW-XV-1 AFW-XV-2 AFW-XV-3
^EOS
MINI-PWR, HPI =
HPI                                              OR   HPI-MDP-FS HPI-MOV-CC
^EOS
