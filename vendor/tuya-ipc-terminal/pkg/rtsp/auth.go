package rtsp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

type rtspAuthorizer struct {
	endpoint, secret string
	client           *http.Client
}
type rtspGrant struct {
	Allowed  bool   `json:"allowed"`
	Ticket   string `json:"ticket"`
	Internal bool   `json:"internal"`
	Admin    bool   `json:"admin"`
}

func newRTSPAuthorizer() *rtspAuthorizer {
	transport := &http.Transport{Proxy: nil, MaxIdleConnsPerHost: 4}
	return &rtspAuthorizer{os.Getenv("TUYA_RTSP_AUTH_URL"), os.Getenv("TUYA_RTSP_AUTH_SECRET"), &http.Client{Timeout: 2 * time.Second, Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
}
func (a *rtspAuthorizer) configured() bool {
	if a == nil || len(a.secret) < 32 {
		return false
	}
	u, err := url.Parse(a.endpoint)
	return err == nil && u.Scheme == "http" && u.Hostname() == "127.0.0.1" && u.Port() != "" && u.User == nil && u.Path == "/authorize" && u.RawQuery == "" && u.Fragment == ""
}
func (a *rtspAuthorizer) check(authorization, ticket, device, address string) rtspGrant {
	if !a.configured() {
		return rtspGrant{}
	}
	data, _ := json.Marshal(map[string]string{"authorization": authorization, "ticket": ticket, "deviceId": device, "address": address})
	request, _ := http.NewRequest("POST", a.endpoint, bytes.NewReader(data))
	request.Header.Set("Authorization", "Bearer "+a.secret)
	request.Header.Set("Content-Type", "application/json")
	response, err := a.client.Do(request)
	if err != nil {
		return rtspGrant{}
	}
	defer response.Body.Close()
	var grant rtspGrant
	if response.StatusCode != 200 || json.NewDecoder(io.LimitReader(response.Body, 8192)).Decode(&grant) != nil {
		return rtspGrant{}
	}
	if !grant.Allowed || len(grant.Ticket) < 16 {
		return rtspGrant{}
	}
	return grant
}
func authHeader(request *RTSPRequest) string {
	for key, value := range request.Headers {
		if strings.EqualFold(key, "Authorization") {
			return value
		}
	}
	return ""
}
func authChallenge(conn net.Conn, request *RTSPRequest) {
	_ = sendRTSPResponse(conn, 401, "Unauthorized", map[string]string{"CSeq": strconv.Itoa(request.CSeq), "WWW-Authenticate": `Basic realm="Tuya Local", charset="UTF-8"`}, "")
}
func (s *RTSPServer) watchAuthorization(ctx context.Context, client *RTSPClient) {
	timer := time.NewTicker(time.Second)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
			grant := s.authorizer.check("", client.authTicket, client.stream.camera.DeviceID, "")
			if !grant.Allowed {
				s.removeClient(client.session)
				return
			}
		}
	}
}
func requestWithinCamera(client *RTSPClient, request *RTSPRequest) bool {
	u, err := url.Parse(request.URL)
	if err != nil || u.User != nil || u.Fragment != "" {
		return false
	}
	if request.URL == "*" && request.Method == "OPTIONS" {
		return true
	}
	base := client.requestPath
	if u.Scheme != "rtsp" || u.Host == "" {
		return false
	}
	return u.Path == client.cameraPath || u.Path == base || u.Path == base+"/video" || u.Path == base+"/audio" || u.Path == base+"/backchannel"
}
func internalRTSPURL(source string) string {
	password := os.Getenv("TUYA_RTSP_INTERNAL_PASSWORD")
	if password == "" {
		return source
	}
	u, err := url.Parse(source)
	if err != nil {
		return source
	}
	u.User = url.UserPassword("_tuya_local_engine", password)
	return u.String()
}

var errMissingRTSPAuthorization = errors.New("RTSP requires the private Tuya Local authorization broker")
