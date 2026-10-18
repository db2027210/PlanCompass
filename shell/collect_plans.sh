#!/bin/bash

# --- Interactive Setup ---
read -p "Enter Docker container name to monitor [og8]: " CONTAINER
CONTAINER=${CONTAINER:-og8}

read -p "Enter the FULL path to the log file INSIDE the container: " LOG_FILE_PATH
if [ -z "$LOG_FILE_PATH" ]; then
    echo "ERROR: Log file path inside the container is required."
    exit 1
fi

read -p "Enter path to save the output plan file [./plans.log]: " SAVE_PATH
SAVE_PATH=${SAVE_PATH:-./plans.log}

echo "Starting to monitor log file '$LOG_FILE_PATH' in container '$CONTAINER'..."
echo "Found plans will be saved to '$SAVE_PATH'. Press Ctrl+C to stop."

# --- Main Logic ---
# Use docker exec to tail the log file and pipe it to a while loop for parsing.
# The stdbuf command is used to prevent buffering issues with the pipe.
docker exec "$CONTAINER" stdbuf -oL tail -f -n 0 "$LOG_FILE_PATH" 2>/dev/null | while IFS= read -r line; do
    # Check for the start of an auto_explain log entry
    if [[ "$line" == *"duration: "* && "$line" == *" plan:"* ]]; then
        
        echo "Found a potential query plan at $(date '+%Y-%m-%d %H:%M:%S')..."
        
        # Extract duration from the log line (e.g., "duration: 10.5 ms")
        duration=$(echo "$line" | sed -n 's/.*duration: *\([0-9.]*\) *ms.*/\1/p')

        # A variable to hold the full JSON plan
        json_plan=""
        # A flag to indicate we are inside a JSON block
        in_json=0
        # A counter for curly braces to handle nested objects
        brace_count=0

        # Now, continue reading lines from the pipe until we have the full JSON object
        # This inner loop reads from the same pipe as the outer loop.
        while IFS= read -r json_line; do
            
            # If we are not in the JSON block yet, look for the first opening brace
            # This handles cases where the "plan:" line is not immediately followed by "{".
            if [[ $in_json -eq 0 && "$json_line" == "{"* ]]; then
                in_json=1
            fi
            
            # If we are inside the JSON block, process the line
            if [ $in_json -eq 1 ]; then
                # Append the line to our JSON string
                json_plan+="$json_line"
                
                # Count opening and closing braces to find the end of the JSON object
                open_braces=$(grep -o "{" <<< "$json_line" | wc -l)
                close_braces=$(grep -o "}" <<< "$json_line" | wc -l)
                brace_count=$((brace_count + open_braces - close_braces))
                
                # If brace_count is 0, we have the complete JSON object
                if [ $brace_count -eq 0 ]; then
                    
                    # Extract the "Query Text" from the JSON plan.
                    # Using grep and sed for basic parsing as jq may not be installed.
                    # This is a best-effort extraction.
                    query_text=$(echo "$json_plan" | grep '"Query Text":' | sed -e 's/.*"Query Text": "//' -e 's/",$//' -e 's/".*//')

                    # Save the results to the specified output file
                    echo "-- Found at: $(date '+%Y-%m-%d %H:%M:%S')" >> "$SAVE_PATH"
                    echo "-- Duration: ${duration} ms" >> "$SAVE_PATH"
                    echo "-- SQL Query:" >> "$SAVE_PATH"
                    echo "$query_text" >> "$SAVE_PATH"
                    echo "-- JSON Plan:" >> "$SAVE_PATH"
                    echo "$json_plan" >> "$SAVE_PATH"
                    echo "--------------------------------------------------" >> "$SAVE_PATH"
                    
                    echo "Successfully saved plan for query: ${query_text:0:80}..."
                    
                    # Break out of this inner loop to wait for the next "duration:" line
                    break
                fi
            fi
        done
    fi
done
