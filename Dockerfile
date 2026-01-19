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

# Create proxy-secret file (required)
RUN echo "$SECRET" > proxy-secret

# Expose internal port
EXPOSE 3128

# Start MTProxy in SINGLE-SECRET MODE (NO proxy-multi.conf)
CMD ["sh", "-c", "./objs/bin/mtproto-proxy -H 3128 -S $SECRET --aes-pwd proxy-secret"]
