# Container image that runs your code 
FROM debian:bookworm

RUN apt-get update && apt-get install -y git python3-full python3-pip python3-venv

WORKDIR /root
RUN git clone "https://x-access-token:${GH_PAT}@github.com/Lemming112/nextcloud-forms-to-tables.git"

WORKDIR /root/nextcloud-forms-to-tables

CMD ["/bin/bash"]
