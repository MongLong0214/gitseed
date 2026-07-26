#!/bin/sh
docker build -t app:latest .
docker run --rm -p 8080:8080 app:latest
