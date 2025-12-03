#!/bin/bash

# Botのメインスクリプトを実行
python3 bot.py

# 注意:
# 1. 多くのLinux環境では 'python' よりも 'python3' コマンドが推奨されます。
#    環境に合わせて 'python' または 'python3' に変更してください。
# 2. このスクリプトはBotが終了すると自動的に終了します。
#    Botをバックグラウンドで永続的に実行したい場合は、別のツール(例: nohup, systemd, screen)が必要です。