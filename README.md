# Aria2GDrive
Hello there, 👽 I am a Telegram Bot that can mirror files to your GDrive.

### Prepare config.env file
Create an env file in [Github Gist](https://gist.github.com/) or any other place but make sure to provide the direct download link of that file.
```sh
PICKLE_FILE_URL = ""
BOT_TOKEN = ""
USER_LIST = '[12345, 67890]'
GDRIVE_FOLDER_ID = 'abcXYZ'
```

### Build and run the docker image
```sh
docker build -t mybot:latest .
docker run -d --name=Aria2GdriveBot -e CONFIG_FILE_URL="github gist link of config.env" --restart=unless-stopped mybot:latest
```