FROM python:3.9.0-slim
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install --assume-yes --quiet --no-install-recommends aria2 wget p7zip p7zip-full libmagic1
RUN wget -qO /usr/bin/qbittorrent-nox https://github.com/userdocs/qbittorrent-nox-static/releases/latest/download/x86_64-qbittorrent-nox
RUN wget -qO /tmp/rar.tar.gz https://www.rarlab.com/rar/rarlinux-x64-620.tar.gz
RUN 7z x /tmp/rar.tar.gz -o/tmp -y
RUN 7z x /tmp/rar.tar -o/tmp -y
RUN cp /tmp/rar/rar /tmp/rar/unrar /usr/bin
RUN chmod ugo+x /usr/bin/qbittorrent-nox /usr/bin/rar /usr/bin/unrar
COPY . /usr/src/app
WORKDIR /usr/src/app
RUN python -m pip install --upgrade pip
RUN python -m pip install --no-cache-dir -r requirements.txt
ENTRYPOINT ["python", "-u", "main.py"]
