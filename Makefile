.PHONY: build test vet lint clean install

VERSION ?= 1.1.1
LDFLAGS = -X github.com/alirezaopmc/promux/pkg/cmd.Version=$(VERSION)

build:
	mkdir -p bin
	CGO_ENABLED=0 go build -ldflags="$(LDFLAGS) -s -w" -o bin/promux ./cmd/promux

test:
	go test -v -race ./...

vet:
	go vet ./...

lint: vet

clean:
	rm -rf bin/

install: build
	install -m 755 bin/promux $(HOME)/.local/bin/promux
