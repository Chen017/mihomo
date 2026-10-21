package encryption

import (
	"bytes"
	"crypto/ecdh"
	"crypto/rand"
	"io"
	"net"
	"testing"
	"time"
)

func ticketTestConn(t *testing.T, wire, currentKey []byte) (*CommonConn, *ClientInstance) {
	t.Helper()
	a, b := net.Pipe()
	t.Cleanup(func() { a.Close() })
	a.SetDeadline(time.Now().Add(5 * time.Second))
	go func() { defer b.Close(); b.Write(wire) }()
	pfs := bytes.Repeat([]byte{1}, 64)
	i := &ClientInstance{PfsKey: currentKey, Expire: time.Now().Add(time.Hour)}
	c := NewCommonConn(a, true)
	c.Client = i
	c.UnitedKey = append(pfs, bytes.Repeat([]byte{2}, 32)...)
	return c, i
}

func TestResumedTicketExpiresBeforeAuthenticatedResponse(t *testing.T) {
	header := make([]byte, 5)
	EncodeHeader(header, 17)
	for name, wire := range map[string][]byte{
		"early EOF":              nil,
		"short random":           make([]byte, 7),
		"invalid header":         make([]byte, 21),
		"short record":           append(make([]byte, 16), header...),
		"unauthenticated record": append(append(make([]byte, 16), header...), make([]byte, 17)...),
	} {
		t.Run(name, func(t *testing.T) {
			c, i := ticketTestConn(t, wire, bytes.Repeat([]byte{1}, 64))
			if _, err := c.Read(make([]byte, 32)); err == nil {
				t.Fatal("expected rejected session")
			}
			if time.Now().Before(i.Expire) {
				t.Fatal("failed 0-RTT ticket remained cached")
			}
		})
	}
}

func TestOldFailureDoesNotExpireNewTicket(t *testing.T) {
	c, i := ticketTestConn(t, nil, bytes.Repeat([]byte{3}, 64))
	c.Read(make([]byte, 32))
	if !time.Now().Before(i.Expire) {
		t.Fatal("old connection expired a newer ticket")
	}
}

func TestAuthenticatedResumptionKeepsTicketAfterClose(t *testing.T) {
	random := bytes.Repeat([]byte{4}, 16)
	key := append(bytes.Repeat([]byte{1}, 64), bytes.Repeat([]byte{2}, 32)...)
	header := make([]byte, 5)
	EncodeHeader(header, 18)
	wire := append(append([]byte{}, random...), NewAEAD(random, key, true).Seal(header, nil, []byte("ok"), header)...)
	c, i := ticketTestConn(t, wire, bytes.Repeat([]byte{1}, 64))
	data := make([]byte, 32)
	if n, err := c.Read(data); err != nil || string(data[:n]) != "ok" {
		t.Fatalf("response: %q %v", data[:n], err)
	}
	if _, err := c.Read(data); err != io.EOF {
		t.Fatalf("close: %v", err)
	}
	if !time.Now().Before(i.Expire) {
		t.Fatal("normal stream close expired a valid ticket")
	}
}

func TestZeroRTTResumptionAndServerRestart(t *testing.T) {
	key, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	server := &ServerInstance{}
	if err := server.Init([][]byte{key.Bytes()}, 2, 600, 0, "100-35-35"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { server.Close() })
	client := &ClientInstance{}
	if err := client.Init([][]byte{key.PublicKey().Bytes()}, 2, 1, "100-35-35"); err != nil {
		t.Fatal(err)
	}
	exchange := func(wantZero, rejected bool) {
		t.Helper()
		listener, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		defer listener.Close()
		a, err := net.Dial("tcp", listener.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		b, err := listener.Accept()
		if err != nil {
			a.Close()
			t.Fatal(err)
		}
		defer a.Close()
		a.SetDeadline(time.Now().Add(5 * time.Second))
		b.SetDeadline(time.Now().Add(5 * time.Second))
		result := make(chan error, 1)
		go func() {
			defer b.Close()
			c, err := server.Handshake(b, nil)
			if err == nil {
				var data [4]byte
				_, err = io.ReadFull(c, data[:])
				if err == nil && string(data[:]) != "ping" {
					err = io.ErrUnexpectedEOF
				}
				if err == nil {
					_, err = c.Write([]byte("pong"))
				}
			}
			result <- err
		}()
		c, err := client.Handshake(a)
		if err != nil {
			t.Fatal(err)
		}
		if (len(c.PreWrite) > 0) != wantZero {
			t.Fatalf("0-RTT=%v, wanted %v", len(c.PreWrite) > 0, wantZero)
		}
		written := make(chan error, 1)
		go func() { _, err := c.Write([]byte("ping")); written <- err }()
		var response [4]byte
		_, err = io.ReadFull(c, response[:])
		a.Close()
		writeErr, serverErr := <-written, <-result
		if rejected {
			if err == nil || serverErr == nil {
				t.Fatal("expired ticket was accepted")
			}
			return
		}
		if err != nil || writeErr != nil || serverErr != nil || string(response[:]) != "pong" {
			t.Fatalf("round trip: %q %v %v %v", response, err, writeErr, serverErr)
		}
	}
	exchange(false, false) // First connection obtains a ticket.
	exchange(true, false)  // Subsequent connection actually takes the zero-RTT path.
	server.RWLock.Lock()
	server.Sessions = make(map[[16]byte]*ServerSession) // Equivalent to losing sessions on restart.
	server.RWLock.Unlock()
	exchange(true, true)
	exchange(false, false) // Reacquire once; the client and its config stay unchanged.
	exchange(true, false)
}
