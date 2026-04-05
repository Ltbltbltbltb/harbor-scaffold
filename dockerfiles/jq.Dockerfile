FROM ubuntu:22.04
RUN apt-get update && apt-get install -y bc python3 jq && rm -rf /var/lib/apt/lists/*
