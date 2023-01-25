FROM python:3.9.0-slim
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install --assume-yes --quiet --no-install-recommends aria2 wget
RUN wget -qO /usr/bin/qbittorrent-nox https://github.com/userdocs/qbittorrent-nox-static/releases/latest/download/x86_64-qbittorrent-nox
RUN chmod ugo+x /usr/bin/qbittorrent-nox
COPY . /usr/src/app
WORKDIR /usr/src/app
RUN python -m pip install --upgrade pip
RUN python -m pip install --no-cache-dir -r requirements.txt
ENTRYPOINT ["python", "-u", "main.py"]
