#!/bin/bash

echo "no" | avdmanager create avd --force -n $1 -k "$2" -d $3
echo "$1 avd created"
# source permission_set.sh
