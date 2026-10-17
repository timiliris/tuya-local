package rtsp

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"sync"
	"time"

	"tuya-ipc-terminal/pkg/core"
	"tuya-ipc-terminal/pkg/storage"
	"tuya-ipc-terminal/pkg/tuya"
)

type RTSPServer struct {
	connectionSlots chan struct{}
	authorizer      *rtspAuthorizer
	port            int
	listener        net.Listener
	storageManager  *storage.StorageManager
	clients         map[string]*RTSPClient
	streams         map[string]*CameraStream
	mutex           sync.RWMutex
	ctx             context.Context
	cancel          context.CancelFunc
	running         bool
}

type RTSPClient struct {
	requestPath          string
	authTicket           string
	authInternal         bool
	authAdmin            bool
	passive              bool
	conn                 net.Conn
	session              string
	cameraPath           string
	stream               *CameraStream
	reader               *bufio.Reader
	transportMode        TransportMode
	videoRTPPort         int
	videoRTCPPort        int
	audioRTPPort         int
	audioRTCPPort        int
	backAudioRTPPort     int // server-side port for back audio
	backAudioRTCPPort    int // server-side port for back audio RTCP
	videoRTPChannel      byte
	videoRTCPChannel     byte
	audioRTPChannel      byte
	audioRTCPChannel     byte
	backAudioRTPChannel  byte
	backAudioRTCPChannel byte
	setupCount           int
}

type CameraStream struct {
	camera         *storage.CameraInfo
	resolution     string
	user           *storage.UserSession
	webrtcBridge   *WebRTCBridge
	clients        map[string]*RTSPClient
	mutex          sync.RWMutex
	startRequested bool
	connecting     bool
	active         bool
	lastActivity   time.Time

	// Delayed shutdown
	shutdownTimer *time.Timer
	shutdownDelay time.Duration

	// Reference to server for cleanup
	server   *RTSPServer
	streamId string
}

type ServerConfig struct {
	Port                 int
	MaxClients           int
	StreamTimeout        time.Duration
	ConnectionTimeout    time.Duration
	EnableAuthentication bool
}

func NewRTSPServer(port int, storageManager *storage.StorageManager) *RTSPServer {
	ctx, cancel := context.WithCancel(context.Background())

	return &RTSPServer{
		connectionSlots: make(chan struct{}, 64),
		authorizer:      newRTSPAuthorizer(),
		port:            port,
		storageManager:  storageManager,
		clients:         make(map[string]*RTSPClient),
		streams:         make(map[string]*CameraStream),
		ctx:             ctx,
		cancel:          cancel,
		running:         false,
	}
}

func (s *RTSPServer) Start() error {
	if !s.authorizer.configured() {
		return errMissingRTSPAuthorization
	}
	s.mutex.Lock()
	defer s.mutex.Unlock()

	if s.running {
		return errors.New("server is already running")
	}

	listener, err := net.Listen("tcp", net.JoinHostPort(os.Getenv("TUYA_RTSP_BIND"), fmt.Sprint(s.port)))
	if err != nil {
		return fmt.Errorf("failed to listen on port %d: %v", s.port, err)
	}

	s.listener = listener
	s.running = true

	core.Logger.Info().Msgf("RTSP Server started on port %d", s.port)
	core.Logger.Info().Msgf("Available endpoints:")

	// List available camera endpoints
	if err := s.printAvailableEndpoints(); err != nil {
		core.Logger.Warn().Msgf("Could not list camera endpoints: %v", err)
	}

	// Start accepting connections
	go s.acceptConnections()

	// Start cleanup routine
	go s.cleanupRoutine()

	return nil
}

func (s *RTSPServer) Stop() error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	if !s.running {
		return errors.New("server is not running")
	}

	core.Logger.Info().Msg("Stopping RTSP server...")

	// Cancel context to stop all goroutines
	s.running = false
	s.cancel()

	// Close listener
	if s.listener != nil {
		s.listener.Close()
	}

	// Close all client connections
	for _, client := range s.clients {
		client.conn.Close()
	}

	// Stop all streams
	for _, stream := range s.streams {
		stream.Stop()
	}

	return nil
}

func (s *RTSPServer) IsRunning() bool {
	s.mutex.RLock()
	defer s.mutex.RUnlock()
	return s.running
}

func (s *RTSPServer) GetPort() int {
	return s.port
}

func (s *RTSPServer) GetStats() ServerStats {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	activeStreams := 0
	for _, stream := range s.streams {
		if stream.active {
			activeStreams++
		}
	}

	return ServerStats{
		Port:         s.port,
		Running:      s.running,
		ClientCount:  len(s.clients),
		StreamCount:  activeStreams,
		TotalStreams: len(s.streams),
	}
}

type ServerStats struct {
	Port         int  `json:"port"`
	Running      bool `json:"running"`
	ClientCount  int  `json:"clientCount"`
	StreamCount  int  `json:"activeStreamCount"`
	TotalStreams int  `json:"totalStreams"`
}

func (s *RTSPServer) acceptConnections() {
	for s.running {
		select {
		case <-s.ctx.Done():
			return
		default:
			conn, err := s.listener.Accept()
			if err != nil {
				if s.running {
					core.Logger.Error().Err(err).Msg("Error accepting connection")
				}
				continue
			}

			select {
			case s.connectionSlots <- struct{}{}:
				go func() { defer func() { <-s.connectionSlots }(); s.handleConnection(conn) }()
			default:
				_ = conn.Close()
			}
		}
	}
}

func (s *RTSPServer) handleConnection(conn net.Conn) {
	defer conn.Close()

	session := generateSessionID()
	core.Logger.Info().Msgf("New RTSP connection established, session=%s", session)

	reader := bufio.NewReader(conn)

	_ = conn.SetReadDeadline(time.Now().Add(15 * time.Second))
	var request *RTSPRequest
	var grant rtspGrant
	var err error
	for attempts := 0; attempts < 8; attempts++ {
		request, err = s.parseRTSPRequestFromReader(reader)
		if err != nil {
			return
		}
		host, _, _ := net.SplitHostPort(conn.RemoteAddr().String())
		grant = s.authorizer.check(authHeader(request), grant.Ticket, "", host)
		if !grant.Allowed {
			authChallenge(conn, request)
			continue
		}
		if request.Method == "OPTIONS" {
			_ = sendRTSPResponse(conn, 200, "OK", map[string]string{"CSeq": fmt.Sprint(request.CSeq), "Public": "OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN"}, "")
			continue
		}
		break
	}
	if request == nil || !grant.Allowed || request.Method == "OPTIONS" {
		return
	}

	parsedRequest, parseErr := url.Parse(request.URL)
	if parseErr != nil || parsedRequest.User != nil || parsedRequest.Fragment != "" || parsedRequest.Scheme != "rtsp" || (request.Method != "DESCRIBE" && request.Method != "SETUP" && request.Method != "PLAY") {
		_ = sendRTSPResponse(conn, 400, "Bad Request", map[string]string{"CSeq": fmt.Sprint(request.CSeq)}, "")
		return
	}
	// Extract camera path from URL
	cameraPath, streamResolution := extractCameraPath(request.URL)
	if cameraPath == "" {
		core.Logger.Error().Msg("Invalid RTSP URL")
		sendRTSPResponse(conn, 400, "Bad Request", nil, "")
		return
	}

	// Find camera
	camera, user, err := s.findCamera(cameraPath)
	if err != nil {
		core.Logger.Error().Msgf("Error finding camera for path %s: %v", cameraPath, err)
		sendRTSPResponse(conn, 500, "Internal Server Error", nil, "")
		return
	}

	if camera == nil {
		core.Logger.Error().Msgf("Camera not found for path %s", cameraPath)
		sendRTSPResponse(conn, 404, "Not Found", nil, "")
		return
	}

	grant = s.authorizer.check("", grant.Ticket, camera.DeviceID, "")
	if !grant.Allowed {
		_ = sendRTSPResponse(conn, 403, "Forbidden", map[string]string{"CSeq": fmt.Sprint(request.CSeq)}, "")
		return
	}
	core.Logger.Info().Msgf("New RTSP connection for camera: %s (%s)", camera.DeviceName, camera.DeviceID)

	// AI observers never start or retain a battery camera session.
	parsed, _ := url.Parse(request.URL)
	aiMode := parsed.Query().Get("ai")
	passive := aiMode == "passive"
	if aiMode != "" {
		if !grant.Internal {
			sendRTSPResponse(conn, 403, "Forbidden", nil, "")
			return
		}
		host, _, _ := net.SplitHostPort(conn.RemoteAddr().String())
		if ip := net.ParseIP(host); ip == nil || !ip.IsLoopback() {
			sendRTSPResponse(conn, 403, "Forbidden", nil, "")
			return
		}
		var skill tuya.Skill
		if json.Unmarshal([]byte(camera.Skill), &skill) != nil || skill.LowPower != 0 || len(skill.Videos) == 0 {
			passive = true
		}
	}
	var stream *CameraStream
	if passive {
		stream = s.observedStream(camera.DeviceID)
	} else {
		stream, err = s.getOrCreateStream(camera, streamResolution, user)
	}
	if passive && stream == nil {
		sendRTSPResponse(conn, 454, "Session Not Found", nil, "")
		return
	}
	if err != nil {
		core.Logger.Error().Err(err).Msgf("Failed to create stream for camera %s", camera.DeviceName)
		sendRTSPResponse(conn, 500, "Internal Server Error", nil, "Failed to create stream")
		return
	}

	// Create RTSP client
	client := &RTSPClient{
		requestPath:         parsedRequest.Path,
		authTicket:          grant.Ticket,
		authInternal:        grant.Internal,
		authAdmin:           grant.Admin,
		passive:             passive,
		conn:                conn,
		reader:              reader,
		session:             session,
		cameraPath:          cameraPath,
		stream:              stream,
		transportMode:       TransportUDP, // Default to UDP
		videoRTPPort:        0,
		audioRTPPort:        0,
		backAudioRTPPort:    0,
		videoRTPChannel:     0,
		audioRTPChannel:     2,
		backAudioRTPChannel: 4,
		setupCount:          0,
	}

	// Add client to server and stream
	s.addClient(client)
	if passive {
		if !stream.addObserver(client) {
			s.removeClient(client.session)
			sendRTSPResponse(conn, 454, "Session Not Found", nil, "")
			return
		}
	} else {
		stream.AddClient(client)
	}

	ctx, cancel := context.WithCancel(s.ctx)
	defer cancel()
	go s.watchAuthorization(ctx, client)
	// Handle initial request.
	if s.handleRTSPMethod(client, request) {
		s.removeClient(client.session)
		return
	}

	// Handle further requests
	s.handleRTSPProtocol(client)
}

func (s *RTSPServer) findCamera(path string) (*storage.CameraInfo, *storage.UserSession, error) {
	cameras, err := s.storageManager.GetAllCameras()
	if err != nil {
		return nil, nil, err
	}

	// Find camera by RTSP path
	for _, camera := range cameras {
		if camera.RTSPPath == path {
			// Get user for this camera
			users, err := s.storageManager.ListUsers()
			if err != nil {
				continue
			}

			for _, user := range users {
				if user.UserKey == camera.UserKey {
					return &camera, &user, nil
				}
			}
		}
	}

	return nil, nil, nil
}

func (s *RTSPServer) getOrCreateStream(camera *storage.CameraInfo, streamResolution string, user *storage.UserSession) (*CameraStream, error) {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	// Check if stream already exists
	streamId := fmt.Sprintf("%s-%s", camera.DeviceID, streamResolution)
	if stream, exists := s.streams[streamId]; exists {
		if stream.active || stream.connecting {
			core.Logger.Trace().Msgf("Reusing existing stream for camera: %s", camera.DeviceName)
			stream.lastActivity = time.Now()
			return stream, nil
		}
	}

	// Create new stream
	stream := NewCameraStream(camera, streamResolution, user, s.storageManager, s)
	stream.connecting = true

	stream.webrtcBridge.OnError = func(err error) {
		core.Logger.Error().Err(err).Msgf("WebRTC error for camera %s", camera.DeviceName)
		go stream.stopStream()
	}

	s.streams[streamId] = stream

	core.Logger.Info().Msgf("Created new stream for camera: %s", camera.DeviceName)
	return stream, nil
}

func (s *RTSPServer) removeStream(streamId string) {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	if _, exists := s.streams[streamId]; exists {
		delete(s.streams, streamId)
		core.Logger.Trace().Msgf("Removed stream %s from server map", streamId)
	}
}

func (s *RTSPServer) addClient(client *RTSPClient) {
	s.mutex.Lock()
	defer s.mutex.Unlock()
	s.clients[client.session] = client
}

func (s *RTSPServer) removeClient(sessionID string) {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	if client, exists := s.clients[sessionID]; exists {
		// Remove client from stream
		if client.stream != nil {
			client.stream.RemoveClient(sessionID)
		}

		client.conn.Close()
		delete(s.clients, sessionID)
	}
}

func (s *RTSPServer) printAvailableEndpoints() error {
	cameras, err := s.storageManager.GetAllCameras()
	if err != nil {
		return err
	}

	if len(cameras) == 0 {
		core.Logger.Warn().Msg("  No cameras available. Run 'cameras refresh' first.")
		return nil
	}

	for _, camera := range cameras {
		var skill *tuya.Skill
		json.Unmarshal([]byte(camera.Skill), &skill)

		supportClarity := skill != nil && (skill.WebRTC&(1<<5)) != 0
		baseUrl := fmt.Sprintf("rtsp://localhost:%d%s", s.port, camera.RTSPPath)

		if supportClarity {
			core.Logger.Info().Msgf("  %s/hd (%s)", baseUrl, camera.DeviceName)
			core.Logger.Info().Msgf("  %s/sd (%s)", baseUrl, camera.DeviceName)
		} else {
			core.Logger.Info().Msgf("  %s (%s)", baseUrl, camera.DeviceName)
		}
	}

	return nil
}

func (s *RTSPServer) cleanupRoutine() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-s.ctx.Done():
			return
		case <-ticker.C:
			s.cleanupInactiveStreams()
		}
	}
}

func (s *RTSPServer) cleanupInactiveStreams() {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	now := time.Now()
	for deviceID, stream := range s.streams {
		// Remove streams inactive for more than 5 minutes
		if now.Sub(stream.lastActivity) > 5*time.Minute && len(stream.clients) == 0 {
			core.Logger.Trace().Msgf("Cleaning up inactive stream for camera: %s", stream.camera.DeviceName)
			stream.Stop()
			delete(s.streams, deviceID)
		}
	}
}

func NewCameraStream(camera *storage.CameraInfo, resolution string, user *storage.UserSession, storageManager *storage.StorageManager, server *RTSPServer) *CameraStream {
	stream := &CameraStream{
		camera:        camera,
		resolution:    resolution,
		user:          user,
		clients:       make(map[string]*RTSPClient),
		active:        false,
		lastActivity:  time.Now(),
		shutdownDelay: 5 * time.Second,
		server:        server,
		streamId:      fmt.Sprintf("%s-%s", camera.DeviceID, resolution),
	}

	stream.webrtcBridge = NewWebRTCBridge(camera, resolution, user, storageManager)

	return stream
}

func (cs *CameraStream) AddClient(client *RTSPClient) {
	cs.mutex.Lock()
	defer cs.mutex.Unlock()

	// Cancel any pending shutdown
	if cs.shutdownTimer != nil {
		cs.shutdownTimer.Stop()
		cs.shutdownTimer = nil
		core.Logger.Trace().Msgf("Cancelled pending shutdown for camera %s - new client connected", cs.camera.DeviceName)
	}

	cs.clients[client.session] = client
	cs.lastActivity = time.Now()

	// Start stream if not active
	if !cs.active && !cs.startRequested {
		cs.startRequested = true
		go cs.startStream()
	}
}

func (cs *CameraStream) RemoveClient(sessionID string) {
	cs.mutex.Lock()
	defer cs.mutex.Unlock()

	// Remove from RTP forwarder
	if cs.webrtcBridge != nil && cs.webrtcBridge.rtpForwarder != nil {
		cs.webrtcBridge.rtpForwarder.RemoveClient(sessionID)
	}

	delete(cs.clients, sessionID)
	if !cs.hasViewer() {
		for _, observer := range cs.clients {
			if observer.passive {
				observer.conn.Close()
			}
		}
	}
	cs.lastActivity = time.Now()

	// Schedule stream shutdown if no clients and stream is active
	if len(cs.clients) == 0 && cs.active {
		cs.scheduleShutdown()
	}
}

func (cs *CameraStream) SetShutdownDelay(delay time.Duration) {
	cs.mutex.Lock()
	defer cs.mutex.Unlock()
	cs.shutdownDelay = delay
}

func (cs *CameraStream) Stop() {
	// Clear all clients first
	for sessionID := range cs.clients {
		cs.RemoveClient(sessionID)
	}

	// Stop the stream
	cs.stopStream()
}

func (cs *CameraStream) startStream() {
	cs.mutex.Lock()
	if cs.active {
		cs.mutex.Unlock()
		return
	}
	cs.connecting = true
	bridge := cs.webrtcBridge
	name := cs.camera.DeviceName
	cs.mutex.Unlock()

	core.Logger.Info().Msgf("Starting stream for camera: %s", name)

	if err := bridge.Start(); err != nil {
		core.Logger.Error().Err(err).Msg("Failed to start WebRTC bridge")
		cs.mutex.Lock()
		cs.stopStreamInternal()
		cs.mutex.Unlock()
		return
	}

	cs.finishStart()
}

func (cs *CameraStream) finishStart() {
	cs.mutex.Lock()
	defer cs.mutex.Unlock()
	if !cs.webrtcBridge.IsConnected() {
		cs.stopStreamInternal()
		return
	}
	cs.connecting = false
	cs.active = true
	if len(cs.clients) == 0 {
		cs.scheduleShutdown()
	}
}

func (cs *CameraStream) stopStream() {
	cs.mutex.Lock()
	defer cs.mutex.Unlock()
	cs.stopStreamInternal()
}

func (cs *CameraStream) stopStreamInternal() {
	// Check if we should actually stop
	if !cs.active && !cs.connecting {
		return
	}

	wasActive := cs.active
	cs.active = false
	cs.connecting = false
	cs.startRequested = false

	// Cancel any pending shutdown
	if cs.shutdownTimer != nil {
		cs.shutdownTimer.Stop()
		cs.shutdownTimer = nil
	}

	// Only log if we were actually active
	if wasActive {
		core.Logger.Info().Msgf("Stopping stream for camera: %s", cs.camera.DeviceName)
	}

	// Stop WebRTC bridge
	if cs.webrtcBridge != nil {
		cs.webrtcBridge.Stop()
	}

	// Remove from server map in a separate goroutine to avoid potential deadlock
	go func() {
		if cs.server != nil {
			cs.server.removeStream(cs.streamId)
		}
	}()
}

func (cs *CameraStream) scheduleShutdown() {
	// Don't schedule if we're not active
	if !cs.active {
		return
	}

	// Cancel any existing timer
	if cs.shutdownTimer != nil {
		cs.shutdownTimer.Stop()
	}

	core.Logger.Trace().Msgf("Scheduling shutdown for camera %s in %v", cs.camera.DeviceName, cs.shutdownDelay)

	cs.shutdownTimer = time.AfterFunc(cs.shutdownDelay, func() {
		cs.mutex.Lock()
		defer cs.mutex.Unlock()

		// Double-check no clients connected during the delay and stream is still active
		if len(cs.clients) == 0 && cs.active {
			core.Logger.Info().Msgf("Executing delayed shutdown for camera %s", cs.camera.DeviceName)
			cs.stopStreamInternal()
		}

		cs.shutdownTimer = nil
	})
}

// Caller holds cs.mutex. Observers are excluded from camera ownership.
func (cs *CameraStream) hasViewer() bool {
	for _, client := range cs.clients {
		if !client.passive {
			return true
		}
	}
	return false
}
func (cs *CameraStream) addObserver(client *RTSPClient) bool {
	cs.mutex.Lock()
	defer cs.mutex.Unlock()
	if !cs.active || !cs.hasViewer() {
		return false
	}
	cs.clients[client.session] = client
	return true
}
func (s *RTSPServer) observedStream(device string) *CameraStream {
	s.mutex.RLock()
	defer s.mutex.RUnlock()
	for _, stream := range s.streams {
		stream.mutex.RLock()
		available := stream.camera.DeviceID == device && stream.active && stream.hasViewer()
		stream.mutex.RUnlock()
		if available {
			return stream
		}
	}
	return nil
}
