use serde_json::{json, Value};
use std::{
    ffi::{CStr, CString},
    io::{Read, Write},
    net::{TcpStream, ToSocketAddrs},
    sync::mpsc::{Receiver, SyncSender},
    thread,
    time::{Duration, Instant},
};

fn copy_c(target: &mut [std::ffi::c_char], value: &str) -> Result<(), ()> {
    if value.len() >= target.len() || value.contains('\0') {
        return Err(());
    }
    for (to, from) in target.iter_mut().zip(value.bytes()) {
        *to = from as _;
    }
    Ok(())
}

pub fn configure(value: &Value) -> Result<(), ()> {
    let ssid = value["ssid"].as_str().ok_or(())?;
    let password = value["password"].as_str().ok_or(())?;
    let host = value["host"].as_str().ok_or(())?;
    let token = value["token"].as_str().ok_or(())?;
    if ssid.is_empty()
        || ssid.len() > 32
        || (!password.is_empty() && !(8..=63).contains(&password.len()))
        || host.is_empty()
        || !token.bytes().all(|b| b.is_ascii_hexdigit())
        || token.len() != 64
    {
        return Err(());
    }
    let port = value["port"]
        .as_u64()
        .filter(|p| (1..=65535).contains(p))
        .ok_or(())? as u16;
    let mut settings: esp_idf_sys::pip_network_settings_t = unsafe { std::mem::zeroed() };
    copy_c(&mut settings.host, host)?;
    copy_c(&mut settings.token, token)?;
    settings.port = port;
    let ssid = CString::new(ssid).map_err(|_| ())?;
    let password = CString::new(password).map_err(|_| ())?;
    let result =
        unsafe { esp_idf_sys::pip_network_save(ssid.as_ptr(), password.as_ptr(), &settings) };
    if result == 0 {
        Ok(())
    } else {
        Err(())
    }
}

pub fn set_host(value: &Value) -> Result<(), ()> {
    let host = CString::new(value["host"].as_str().ok_or(())?).map_err(|_| ())?;
    let port = value["port"]
        .as_u64()
        .filter(|p| (1..=65535).contains(p))
        .ok_or(())? as u16;
    let result = unsafe { esp_idf_sys::pip_network_set_host(host.as_ptr(), port) };
    if result == 0 {
        Ok(())
    } else {
        Err(())
    }
}

fn session(incoming: &SyncSender<String>, outgoing: &Receiver<String>) -> std::io::Result<()> {
    let mut settings: esp_idf_sys::pip_network_settings_t = unsafe { std::mem::zeroed() };
    if unsafe { esp_idf_sys::pip_network_load(&mut settings) } != 0 {
        return Err(std::io::ErrorKind::NotConnected.into());
    }
    let generation = unsafe { esp_idf_sys::pip_network_generation() };
    let host = unsafe { CStr::from_ptr(settings.host.as_ptr()) }.to_string_lossy();
    let token = unsafe { CStr::from_ptr(settings.token.as_ptr()) }.to_string_lossy();
    let address = (host.as_ref(), settings.port)
        .to_socket_addrs()?
        .next()
        .ok_or(std::io::ErrorKind::AddrNotAvailable)?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(3))?;
    stream.set_read_timeout(Some(Duration::from_millis(250)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    stream.set_nodelay(true)?;
    writeln!(
        stream,
        "{}",
        json!({"type":"hello", "version":1, "device":"pip", "token":token})
    )?;
    let mut bytes = [0u8; 512];
    let mut line = Vec::with_capacity(4096);
    let mut ping = Instant::now();
    let mut received = Instant::now();
    while unsafe { esp_idf_sys::pip_network_ready() } != 0
        && unsafe { esp_idf_sys::pip_network_generation() } == generation
        && received.elapsed() < Duration::from_secs(30)
    {
        for message in outgoing.try_iter().take(8) {
            writeln!(stream, "{message}")?;
        }
        if ping.elapsed() >= Duration::from_secs(10) {
            writeln!(stream, "{{\"type\":\"ping\"}}")?;
            ping = Instant::now();
        }
        match stream.read(&mut bytes) {
            Ok(0) => break,
            Ok(count) => {
                received = Instant::now();
                for byte in &bytes[..count] {
                    if *byte == b'\n' {
                        let message = String::from_utf8(line.clone())
                            .map_err(|_| std::io::ErrorKind::InvalidData)?;
                        incoming
                            .send(message)
                            .map_err(|_| std::io::ErrorKind::BrokenPipe)?;
                        line.clear();
                    } else if line.len() < 4095 {
                        line.push(*byte);
                    } else {
                        return Err(std::io::ErrorKind::InvalidData.into());
                    }
                }
            }
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
                ) => {}
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

pub fn run(incoming: SyncSender<String>, outgoing: Receiver<String>) {
    loop {
        if unsafe { esp_idf_sys::pip_network_ready() } != 0 {
            let _ = session(&incoming, &outgoing);
        }
        thread::sleep(Duration::from_secs(2));
    }
}
