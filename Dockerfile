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

# Create required config files with CORRECT SYNTAX
RUN echo "$SECRET" > proxy-secret && \
    printf "proxy 0.0.0.0:3128 {\n  secret %s;\n}\n" "$SECRET" > proxy-multi.conf

# Expose internal port
EXPOSE 3128

# Start MTProxy
CMD ["sh", "-c", "./objs/bin/mtproto-proxy -H 3128 -S $SECRET --aes-pwd proxy-secret proxy-multi.conf"]
