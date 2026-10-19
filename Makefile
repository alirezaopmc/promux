.PHONY: build test clean install

VERSION ?= 1.0.0
LDFLAGS = -X github.com/alirezaopmc/promux/pkg/cmd.Version=$(VERSION)

build:
	mkdir -p bin
	CGO_ENABLED=0 go build -ldflags="$(LDFLAGS) -s -w" -o bin/promux ./cmd/promux

test:
	go test -v -race ./...

clean:
	rm -rf bin/

install: build
	install -m 755 bin/promux $(HOME)/.local/bin/promux
