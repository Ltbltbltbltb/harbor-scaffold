FROM ubuntu:22.04
RUN apt-get update && apt-get install -y bc python3 && rm -rf /var/lib/apt/lists/*
# TODO: adicione pacotes extras necessarios para seus verifiers
# RUN apt-get update && apt-get install -y nodejs curl && rm -rf /var/lib/apt/lists/*
