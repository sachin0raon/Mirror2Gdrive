FROM python:3.9.0-slim
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install --assume-yes --quiet --no-install-recommends aria2
COPY . /usr/src/app
WORKDIR /usr/src/app
RUN pip3 install --no-cache-dir -r requirements.txt
ENTRYPOINT ["python", "-u", "main.py"]
