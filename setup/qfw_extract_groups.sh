#!/bin/bash

# Initialize an empty array to store the groups
declare -a groups

found_group=0

# Loop through all environment variables
for var in "${!SLURM_JOB_NODELIST_HET_GROUP_@}"; do
	# Extract the group number from the variable name
	group_number=${var#SLURM_JOB_NODELIST_HET_GROUP_}
	# Extract the hostname
	hostname=${!var}
	# Append to the array with format GROUP_<number>=<hostname>
	groups+=("GROUP_${group_number}=${hostname}")

	((found_group++))
done

# Check if any group was found
if [ "$found_group" -eq 0 ]; then
	# If no groups were found, set result to "GROUP_0:GROUP_1"
	result="GROUP_0=$(hostname):GROUP_1=$(hostname)"
else
	# Join array elements with colon
	result="${groups[*]}"
	# Replace space with colon for proper format
	result="${result// /:}"
fi

# Print the result
echo "$result"

exit 0
