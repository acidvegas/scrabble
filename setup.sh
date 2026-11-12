#!/bin/sh
# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# setup.sh

set -e
set --
if [ -n "$ARCADE_URL" ]; then
	set -- --network arcade -e ARCADE_URL -e ARCADE_PUBLIC_URL=${ARCADE_PUBLIC_URL:-https://arcade.\{domain\}} -e ARCADE_GAME=scrabble
fi
if [ -n "$SCRABBLE_PUBLIC_URL" ]; then
	set -- "$@" -e SCRABBLE_PUBLIC_URL
fi
docker build -t scrabble .
docker rm -f scrabble 2>/dev/null || true
docker run -d \
	--name scrabble \
	--restart unless-stopped \
	-p 127.0.0.1:1337:1337 \
	-v scrabble_data:/data \
	"$@" \
	scrabble
