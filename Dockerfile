FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y \
        git \
        build-essential \
        libssl-dev \
        zlib1g-dev \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt

# Clone official Telegram MTProxy
RUN git clone https://github.com/TelegramMessenger/MTProxy.git

WORKDIR /opt/MTProxy

# Build MTProxy
RUN make

# Required runtime files
RUN echo "$SECRET" > proxy-secret
RUN echo "proxy 0.0.0.0:3128;" > proxy.conf

EXPOSE 3128

# IMPORTANT: limit connections to avoid rlimit failure on Koyeb
CMD ["sh", "-c", "./objs/bin/mtproto-proxy -H 3128 -S $SECRET --aes-pwd proxy-secret -c 1024 proxy.conf"]
