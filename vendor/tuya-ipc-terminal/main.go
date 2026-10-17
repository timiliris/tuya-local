package main

import (
	"fmt"
	"os"

	"tuya-ipc-terminal/cmd"
	"tuya-ipc-terminal/pkg/core"
	"tuya-ipc-terminal/pkg/rtsp"
)

const VERSION = "0.0.6"

func main() {
	if len(os.Args) == 2 && os.Args[1] == "browser-player" {
		if err := rtsp.RunBrowserPlayer(os.Stdin, os.Stdout); err != nil {
			os.Exit(1)
		}
		return
	}
	core.InitLogger()

	if err := cmd.Execute(VERSION); err != nil {
		fmt.Println("Command execution failed")
		os.Exit(1)
	}
}
