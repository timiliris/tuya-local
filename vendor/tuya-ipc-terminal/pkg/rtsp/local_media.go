package rtsp

import (
	"net/netip"
	"os"
	"strings"
)

func localMediaOnly() bool { return os.Getenv("TUYA_LOCAL_MEDIA_ONLY") == "1" }

func localCandidate(candidate string) bool {
	fields := strings.Fields(strings.TrimPrefix(strings.TrimSpace(candidate), "a="))
	if len(fields) < 8 || !strings.HasPrefix(fields[0], "candidate:") || fields[6] != "typ" || fields[7] == "relay" {
		return false
	}
	address, err := netip.ParseAddr(fields[4])
	return err == nil && address.Unmap().IsPrivate()
}

func localSDP(description string) string {
	var lines []string
	for _, line := range strings.Split(description, "\r\n") {
		if strings.HasPrefix(line, "a=candidate:") && !localCandidate(line) {
			continue
		}
		lines = append(lines, line)
	}
	return strings.Join(lines, "\r\n")
}
