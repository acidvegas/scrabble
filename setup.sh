#!/bin/sh
# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# setup.sh

set -e
docker build -t scrabble .
docker rm -f scrabble 2>/dev/null || true
docker run -d \
	--name scrabble \
	--restart unless-stopped \
	-p 127.0.0.1:1337:1337 \
	-v scrabble_data:/data \
	scrabble
