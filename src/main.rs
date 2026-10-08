mod network;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    ffi::{CStr, CString},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

#[derive(Clone, Deserialize, Serialize)]
struct OptionItem {
    id: String,
    label: String,
}

#[derive(Clone, Deserialize, Serialize)]
struct Card {
    id: String,
    title: String,
    body: String,
    options: Vec<OptionItem>,
}

fn display_text(text: &str, limit: usize) -> bool {
    !text.is_empty()
        && text.len() <= limit
        && text.bytes().all(|c| c == b'\n' || (32..=126).contains(&c))
}

impl Card {
    fn valid(&self) -> bool {
        display_text(&self.id, 64)
            && display_text(&self.title, 60)
            && display_text(&self.body, 600)
            && (1..=3).contains(&self.options.len())
            && self.options.iter().enumerate().all(|(i, o)| {
                display_text(&o.id, 48)
                    && display_text(&o.label, 24)
                    && !self.options[..i].iter().any(|previous| previous.id == o.id)
            })
    }

    fn show(&self) -> Result<(), ()> {
        if !self.valid() {
            return Err(());
        }
        let title = CString::new(self.title.as_str()).map_err(|_| ())?;
        let body = CString::new(self.body.as_str()).map_err(|_| ())?;
        let labels: Vec<_> = self
            .options
            .iter()
            .map(|o| CString::new(o.label.as_str()).unwrap())
            .collect();
        let pointers: Vec<_> = labels.iter().map(|label| label.as_ptr()).collect();
        let result = unsafe {
            esp_idf_sys::pip_ui_card(
                title.as_ptr(),
                body.as_ptr(),
                pointers.as_ptr(),
                pointers.len() as u32,
            )
        };
        if result == 0 {
            Ok(())
        } else {
            Err(())
        }
    }
}

fn status(text: &str) {
    if let Ok(text) = CString::new(text) {
        unsafe { esp_idf_sys::pip_ui_status(text.as_ptr()) };
    }
}

struct App {
    card: Option<Card>,
    pending: Option<Value>,
    last_reply: Instant,
}

impl App {
    fn receive(&mut self, line: &str) {
        let Ok(message) = serde_json::from_str::<Value>(line) else {
            return;
        };
        match message["type"].as_str() {
            Some("set_host") => {
                let result = network::set_host(&message);
                println!(
                    "PIPEVENT {}",
                    json!({"type":"configured", "ok":result.is_ok()})
                );
            }
            Some("configure") => {
                // Never log credentials or supplied JSON on errors.
                let result = network::configure(&message);
                println!(
                    "PIPEVENT {}",
                    json!({"type":"configured", "ok":result.is_ok()})
                );
            }
            Some("card") => {
                if let Ok(card) = serde_json::from_value::<Card>(message) {
                    if self
                        .card
                        .as_ref()
                        .is_some_and(|current| current.id == card.id)
                    {
                        return;
                    }
                    if self.pending.is_none() && card.show().is_ok() {
                        self.card = Some(card);
                    }
                }
            }
            Some("ack") => {
                if self.pending.as_ref().is_some_and(|reply| {
                    reply["card_id"] == message["card_id"]
                        && reply["option_id"] == message["option_id"]
                }) {
                    self.pending = None;
                    status("Sent to pip");
                }
            }
            _ => {}
        }
    }
}

fn main() {
    esp_idf_sys::link_patches();
    println!("pip: native Rust + ESP-IDF; starting display");
    assert_eq!(
        unsafe { esp_idf_sys::pip_board_init() },
        0,
        "Board initialization failed"
    );
    unsafe { esp_idf_sys::pip_print_diagnostics() };
    let network_result = unsafe { esp_idf_sys::pip_network_init() };
    if network_result != 0 {
        println!("pip: network setup unavailable ({network_result:#x}); USB remains available");
    }
    let (from_network, incoming) = mpsc::sync_channel(8);
    let (outgoing, to_network) = mpsc::sync_channel(8);
    thread::Builder::new()
        .name("pip-network".into())
        .stack_size(12288)
        .spawn(move || network::run(from_network, to_network))
        .expect("network task");
    println!("pip: ready; USB cards and screenshots available");
    let mut app = App {
        card: None,
        pending: None,
        last_reply: Instant::now(),
    };
    let mut taps = 0;
    loop {
        thread::sleep(Duration::from_millis(25));
        unsafe { esp_idf_sys::pip_network_poll() };
        let command = unsafe { esp_idf_sys::pip_debug_poll() };
        if !command.is_null() {
            if let Ok(line) = unsafe { CStr::from_ptr(command) }.to_str() {
                app.receive(line);
            }
        }
        for line in incoming.try_iter().take(8) {
            app.receive(&line);
        }
        let choice = unsafe { esp_idf_sys::pip_ui_choice() } as usize;
        if choice != 0 && app.pending.is_none() {
            if let Some(card) = &app.card {
                if let Some(option) = card.options.get(choice - 1) {
                    app.pending =
                        Some(json!({"type":"choice", "card_id":card.id, "option_id":option.id}));
                    app.last_reply = Instant::now() - Duration::from_secs(5);
                    status("Sending your choice...");
                }
            }
        }
        if app.last_reply.elapsed() >= Duration::from_secs(3) {
            if let Some(reply) = &app.pending {
                let line = reply.to_string();
                println!("PIPEVENT {line}");
                let _ = outgoing.try_send(line);
                app.last_reply = Instant::now();
            }
        }
        let next = unsafe { esp_idf_sys::pip_touch_count() };
        if next != taps && app.card.is_none() {
            taps = next;
            unsafe {
                esp_idf_sys::pip_ui_show(
                    c"hello".as_ptr(),
                    if taps % 2 == 0 { 0xf5f5f0 } else { 0xb6caff },
                )
            };
        }
    }
}
