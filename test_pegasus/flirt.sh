#!/usr/bin/env bash

export FSL_DIR="/WORK/FSL/fsl"
source ${FSL_DIR}/etc/fslconf/fsl.sh

/WORK/FSL/fsl/bin/flirt -in $1 -ref $2 -omat $3 -out $4 -dof 12 -searchrx -180 180 -searchry -180 180 -searchrz -180 180 -cost mutualinfo