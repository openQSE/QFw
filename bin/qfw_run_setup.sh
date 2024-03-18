#!/bin/bash

source $HOME/QFwTmp/venv/bin/activate

python3 $QFW_PATH/bin/qfw_setup.py --groups "$1" \
	--use "/sw/frontier/qhpc/modules/" --mods "quantum/qsim" \
	--python-env "$HOME/QFwTmp/venv/bin/activate"

