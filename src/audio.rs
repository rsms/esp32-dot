use serde_json::json;
use std::{
    ffi::CStr,
    io::{BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    thread,
    time::{Duration, Instant},
};

// A separate authenticated TCP stream carries 20 ms PCM16 packets. A u16 BE
// length precedes each packet; 0=finish, 65535=cancel. No audio goes over USB.
struct Endpoint {
    address: SocketAddr,
    token: String,
    network_generation: u32,
}

fn endpoint() -> std::io::Result<Endpoint> {
    let mut settings: esp_idf_sys::pip_network_settings_t = unsafe { std::mem::zeroed() };
    if unsafe { esp_idf_sys::pip_network_load(&mut settings) } != 0 {
        return Err(std::io::ErrorKind::NotConnected.into());
    }
    let token = unsafe { CStr::from_ptr(settings.token.as_ptr()) }.to_string_lossy();
    let mut address = crate::network::server_address().ok_or(std::io::ErrorKind::NotConnected)?;
    address.set_port(
        settings
            .port
            .checked_add(3)
            .ok_or(std::io::ErrorKind::InvalidInput)?,
    );
    Ok(Endpoint {
        address,
        token: token.into_owned(),
        network_generation: unsafe { esp_idf_sys::pip_network_generation() },
    })
}

fn record(generation: u32, endpoint: &Endpoint) -> std::io::Result<()> {
    let address = endpoint.address;
    let token = &endpoint.token;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))?;
    stream.set_nodelay(true)?;
    stream.set_write_timeout(Some(Duration::from_millis(250)))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    let id = format!("voice-{}-{generation}", unsafe {
        esp_idf_sys::esp_timer_get_time()
    });
    let mut tuning = [0 as std::ffi::c_char; 65];
    unsafe { esp_idf_sys::pip_ui_audio_tuning(tuning.as_mut_ptr(), tuning.len() as u32) };
    let tuning = unsafe { CStr::from_ptr(tuning.as_ptr()) }.to_string_lossy();
    writeln!(
        stream,
        "{}",
        json!({"type":"audio", "version":1, "token":token,
        "id":id, "tuning_id":tuning, "sample_rate":16000, "channels":1, "format":"s16le"})
    )?;
    let mut reader = BufReader::new(stream);
    let mut response = String::new();
    reader.read_line(&mut response)?;
    if serde_json::from_str::<serde_json::Value>(&response)
        .ok()
        .and_then(|v| v["ready"].as_bool())
        != Some(true)
    {
        return Err(std::io::ErrorKind::PermissionDenied.into());
    }
    if unsafe { esp_idf_sys::pip_ui_audio_state() } != (generation | 1) {
        reader.get_mut().write_all(&u16::MAX.to_be_bytes())?;
        return Err(std::io::ErrorKind::Interrupted.into());
    }
    let codec = unsafe { esp_idf_sys::pip_audio_open() };
    if codec != 0 {
        println!("pip: microphone open failed ({codec})");
        return Err(std::io::ErrorKind::Other.into());
    }
    let result: std::io::Result<()> = (|| {
        let mut samples = [0i16; 320];
        let began = Instant::now();
        let mut blocks = 0;
        let mut packet = [0u8; 642];
        packet[..2].copy_from_slice(&640u16.to_be_bytes());
        while unsafe { esp_idf_sys::pip_ui_audio_state() } == (generation | 1)
            && blocks < 1500
            && began.elapsed() < Duration::from_secs(30)
        {
            let read = unsafe { esp_idf_sys::pip_audio_read(samples.as_mut_ptr(), 320) };
            if read != 0 {
                println!("pip: microphone read failed ({read})");
                return Err(std::io::ErrorKind::Other.into());
            }
            for (bytes, sample) in packet[2..].chunks_exact_mut(2).zip(samples.iter()) {
                bytes.copy_from_slice(&sample.to_le_bytes());
            }
            reader.get_mut().write_all(&packet)?;
            if blocks == 0 {
                unsafe { esp_idf_sys::pip_ui_audio_started(generation) };
            }
            blocks += 1;
        }
        let status = unsafe { esp_idf_sys::pip_ui_audio_state() };
        let finish = status == (generation | 2) || status == (generation | 1);
        if status == (generation | 1) {
            unsafe { esp_idf_sys::pip_ui_audio_finish(generation) };
        }
        reader
            .get_mut()
            .write_all(&(if finish { 0u16 } else { u16::MAX }).to_be_bytes())?;
        Ok(())
    })();
    unsafe { esp_idf_sys::pip_audio_close() };
    result?;
    // Wait off the display task, retaining responsiveness during transcription.
    reader
        .get_ref()
        .set_read_timeout(Some(Duration::from_millis(100)))?;
    let began = Instant::now();
    let mut bytes = [0u8; 1024];
    let mut reply = Vec::with_capacity(512);
    while began.elapsed() < Duration::from_secs(50) {
        let status = unsafe { esp_idf_sys::pip_ui_audio_state() };
        if status != (generation | 2) {
            return Ok(());
        }
        match reader.read(&mut bytes) {
            Ok(0) => return Err(std::io::ErrorKind::UnexpectedEof.into()),
            Ok(n) => {
                reply.extend_from_slice(&bytes[..n]);
                if reply.len() > 4096 {
                    return Err(std::io::ErrorKind::InvalidData.into());
                }
                if bytes[..n].contains(&b'\n') {
                    let value: serde_json::Value = serde_json::from_slice(&reply)
                        .map_err(|_| std::io::ErrorKind::InvalidData)?;
                    return if value.get("error").is_some() {
                        Err(std::io::ErrorKind::Other.into())
                    } else {
                        Ok(())
                    };
                }
            }
            Err(e)
                if matches!(
                    e.kind(),
                    std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
                ) => {}
            Err(e) => return Err(e),
        }
    }
    Err(std::io::ErrorKind::TimedOut.into())
}

pub fn run() {
    let mut previous = 0;
    let mut cached: Option<Endpoint> = None;
    let mut retry = Instant::now() - Duration::from_secs(3);
    loop {
        let network_generation = unsafe { esp_idf_sys::pip_network_generation() };
        if cached
            .as_ref()
            .is_some_and(|e| e.network_generation != network_generation)
        {
            cached = None;
        }
        if cached.is_none()
            && unsafe { esp_idf_sys::pip_network_ready() } != 0
            && retry.elapsed() > Duration::from_secs(2)
        {
            // Cache the already-resolved control endpoint before the next tap.
            cached = endpoint().ok();
            retry = Instant::now();
        }
        let state = unsafe { esp_idf_sys::pip_ui_audio_state() };
        let generation = state & !3;
        if state & 3 == 2 && generation != previous {
            previous = generation;
            unsafe { esp_idf_sys::pip_ui_audio_complete(generation, 0) };
        }
        if state & 3 == 1 && generation != previous {
            previous = generation;
            let result = cached
                .as_ref()
                .ok_or_else(|| std::io::Error::from(std::io::ErrorKind::NotConnected))
                .and_then(|endpoint| record(generation, endpoint));
            let success = result.is_ok();
            if let Err(error) = result {
                // Releasing before the microphone opens is a normal gesture,
                // not an endpoint failure. Keep the cache for the next hold.
                if error.kind() != std::io::ErrorKind::Interrupted {
                    println!("pip: audio unavailable or interrupted ({:?})", error.kind());
                    cached = None;
                }
            }
            unsafe { esp_idf_sys::pip_ui_audio_complete(generation, success as u32) };
        }
        thread::sleep(Duration::from_millis(10));
    }
}
