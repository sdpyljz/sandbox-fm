#!/bin/bash
MPLBACKEND=qt5agg

model=../models/Lent/FlowFM.mdu

mmi-runner dflowfm $model --port 62000 --pause -o s1 -o bl -o ucx -o ucy -o zk &
sandbox-fm run --mmi tcp://localhost:62000  $model &


# move sandbox to correct screen

# list all windows
# wmctrl -l
# pick the with the sandbox, store in $win

# wmctrl -r sandbox-fm -b remove,fullscreen

# Select window for actions
# wmctrl -r $win
# Switch to desktop 2
# wmctrl -s 2
# move window to current desktop
# wmctrl -R $win
# wmctrl -a
