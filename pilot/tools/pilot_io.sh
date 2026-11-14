#!/bin/bash
# usage: pilot_io.sh export|import ROUND PROBLEMS [VARIANTS]
# export: writes pending prompts and mirrors them to /tmp/pio/<n>/prompt.txt (neutral paths for the sub-agents)
# import: copies /tmp/pio/<n>/response.txt back, imports, shows validity
set -e
cd /home/claude/lunar-llm-planning
act=$1; rnd=$2; probs=$3; vars=${4:-standard}
RD=pilot/$rnd
if [ "$act" = import ]; then
  while read n d; do
    [ -s /tmp/pio/$n/response.txt ] && cp /tmp/pio/$n/response.txt "$d/response.txt" || echo "MISSING response $n $d"
  done < /tmp/pio/map.txt
  for m in pilot-weak pilot-strong; do python -m src.manual import --model $m --config-dir pilot/config --results-dir $RD --problems $probs --variants $vars; done
fi
rm -rf /tmp/pio; mkdir -p /tmp/pio; n=0; : > /tmp/pio/map.txt
for m in pilot-weak pilot-strong; do
  python -m src.manual export --model $m --config-dir pilot/config --results-dir $RD --problems $probs --variants $vars | grep 'prompt written' | sed 's/prompt written: //;s#/prompt.txt##' | while read d; do
    n=$(( $(wc -l < /tmp/pio/map.txt) + 1 )); mkdir -p /tmp/pio/$n; cp "$d/prompt.txt" /tmp/pio/$n/prompt.txt; echo "$n $d" >> /tmp/pio/map.txt
  done
done
echo "PENDING:"; sed "s#$PWD/##" /tmp/pio/map.txt
