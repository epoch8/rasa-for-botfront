VERSION=$(shell cat version)

IMAGE=ghcr.io/epoch8/rasa-for-botfront/rasa:${VERSION}

build:
	docker build -t ${IMAGE} -f docker/Dockerfile.botfront .

upload:
	docker push ${IMAGE}
