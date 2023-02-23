FROM python:3.10-slim
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install --assume-yes --quiet --no-install-recommends aria2 wget p7zip p7zip-full libmagic1
RUN wget -qO /usr/bin/qbittorrent-nox https://github.com/userdocs/qbittorrent-nox-static/releases/latest/download/x86_64-qbittorrent-nox
RUN wget -qO /tmp/rar.tar.gz https://www.rarlab.com/rar/rarlinux-x64-620.tar.gz
RUN wget -qO /tmp/ffmpeg.zip https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/v4.4.1/ffmpeg-4.4.1-linux-64.zip
RUN wget -qO /tmp/ffprobe.zip https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/v4.4.1/ffprobe-4.4.1-linux-64.zip
RUN 7z x /tmp/rar.tar.gz -o/tmp -y
RUN 7z x /tmp/rar.tar -o/tmp -y
RUN 7z x /tmp/ffmpeg.zip -o/tmp -y
RUN 7z x /tmp/ffprobe.zip -o/tmp -y
RUN cp /tmp/rar/rar /tmp/rar/unrar /tmp/ffmpeg /tmp/ffprobe /usr/bin
RUN chmod ugo+x /usr/bin/qbittorrent-nox /usr/bin/rar /usr/bin/unrar /usr/bin/ffmpeg /usr/bin/ffprobe
RUN rm /tmp/rar/rar /tmp/rar/unrar /tmp/ffmpeg /tmp/ffprobe /tmp/rar.tar.gz /tmp/rar.tar /tmp/ffmpeg.zip /tmp/ffprobe.zip
COPY . /usr/src/app
WORKDIR /usr/src/app
RUN python -m pip install --upgrade pip
RUN python -m pip install --no-cache-dir -r requirements.txt
ENTRYPOINT ["python", "-u", "main.py"]
