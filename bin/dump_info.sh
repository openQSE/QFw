#!/bin/bash

source $HOME/QFwTmp/venv/bin/activate

random_number=$(( RANDOM % 100 + 1 ))

hostname=$(hostname)
echo "HOSTNAME = $hostname" > "$HOME/QFwTmp/out_$random_number"
echo "***********Running prte***********" >> "$HOME/QFwTmp/out_$random_number"
ps -aux | grep prte >>  "$HOME/QFwTmp/out_$random_number"
ps -aux | grep srun >>  "$HOME/QFwTmp/out_$random_number"
echo "***********Running DEFw***********" >>  "$HOME/QFwTmp/out_$random_number"
ps -aux | grep python3 >>  "$HOME/QFwTmp/out_$random_number"
echo "***********DEFw Path***********" >>  "$HOME/QFwTmp/out_$random_number"
which python3 >>  "$HOME/QFwTmp/out_$random_number"
echo "***********module list***********" >>  "$HOME/QFwTmp/out_$random_number"
module list  &>>  "$HOME/QFwTmp/out_$random_number"
echo "==================================" >> "$HOME/QFwTmp/out_$random_number"

