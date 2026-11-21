//! A stand-in model service that records every request the owner sends.
//!
//! It answers the OpenAI-compatible chat API the way `scripts/qa/standin_model_endpoint.py`
//! does: one model, fixed narration, and a Campaign File tool call when that tool is offered
//! and the last message is not a tool result. Unlike that script it keeps the whole request
//! body, because what the model is sent is one of the three things the port must reproduce.
//! No real model, credential or network is involved.

use serde_json::json;
use std::sync::{Arc, Mutex};
use sylvia_base::Json;
use tiny_http::{Header, Method, Response, Server};

/// Model identifier the stand-in lists.
pub const MODEL: &str = "standin-narrator";
/// API key the owner must present.
pub const KEY: &str = "standin-key";
const CONTEXT_LENGTH: u64 = 4_000_000;
const NARRATION: &str = "The stand-in narrator answers. A lantern sways over the crossroads inn as you arrive, and the keeper waves you toward the fire. What do you do?";
const TURN_REPLY: &str = "The stand-in narrator resolves your action. The keeper nods, pours a cup of tea and slides it across the table. What do you do next?";
const CAMPAIGN_FILE_TOOL: &str = "update_campaign_file";
/// Fixed so that anything the owner stores from a reply is the same on every run.
const CREATED: u64 = 1_700_000_000;

/// A running stand-in.
pub struct Standin {
    server: Arc<Server>,
    requests: Arc<Mutex<Vec<Json>>>,
    worker: Option<std::thread::JoinHandle<()>>,
    /// Base URL to give the owner, ending in `/v1`.
    pub url: String,
}

impl Standin {
    /// Listen on a free loopback port.
    pub fn start() -> Result<Self, String> {
        let server = Arc::new(
            Server::http("127.0.0.1:0").map_err(|error| format!("stand-in could not listen: {error}"))?,
        );
        let port = server.server_addr().to_ip().ok_or("stand-in has no IP address")?.port();
        let requests = Arc::new(Mutex::new(Vec::new()));
        let worker = {
            let server = Arc::clone(&server);
            let requests = Arc::clone(&requests);
            std::thread::spawn(move || serve(&server, &requests))
        };
        Ok(Self { server, requests, worker: Some(worker), url: format!("http://127.0.0.1:{port}/v1") })
    }

    /// Stop listening and return every recorded request, in arrival order.
    pub fn finish(mut self) -> Vec<Json> {
        self.server.unblock();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        std::mem::take(&mut *self.requests.lock().expect("request log lock"))
    }
}

fn serve(server: &Server, requests: &Mutex<Vec<Json>>) {
    for mut request in server.incoming_requests() {
        let method = request.method().clone();
        let path = request.url().to_owned();
        let authorised = request.headers().iter().any(|header| {
            header.field.equiv("Authorization") && header.value.as_str() == format!("Bearer {KEY}")
        });
        let mut raw = String::new();
        let _ = request.as_reader().read_to_string(&mut raw);
        let body: Json = serde_json::from_str(&raw).unwrap_or(Json::Null);
        requests.lock().expect("request log lock").push(json!({
            "method": method.as_str(),
            "path": path,
            "authorised": authorised,
            "body": body,
        }));

        let trimmed = path.trim_end_matches('/');
        let response = if !authorised {
            json_response(401, &json!({"error": {"message": "stand-in: wrong or missing API key"}}))
        } else if method == Method::Get && trimmed.ends_with("/models") {
            json_response(
                200,
                &json!({"object": "list", "data": [
                    {"id": MODEL, "object": "model", "context_length": CONTEXT_LENGTH},
                ]}),
            )
        } else if method == Method::Post && trimmed.ends_with("/chat/completions") {
            let reply = plan_reply(&body);
            if body.get("stream").and_then(Json::as_bool).unwrap_or(false) {
                stream_response(&reply)
            } else {
                json_response(200, &completion_body(&reply))
            }
        } else {
            json_response(404, &json!({"error": {"message": "stand-in: unknown path"}}))
        };
        let _ = request.respond(response);
    }
}

enum Reply {
    ToolCall { tool: String, arguments: String },
    Text(&'static str),
}

fn campaign_file_tool_name(request: &Json) -> Option<String> {
    request.get("tools")?.as_array()?.iter().find_map(|tool| {
        let name = tool.get("function")?.get("name")?.as_str()?;
        (name == CAMPAIGN_FILE_TOOL || name.ends_with(&format!("_{CAMPAIGN_FILE_TOOL}")))
            .then(|| name.to_owned())
    })
}

fn plan_reply(request: &Json) -> Reply {
    let tool = campaign_file_tool_name(request);
    let last_is_tool_result = request
        .get("messages")
        .and_then(Json::as_array)
        .and_then(|messages| messages.last())
        .and_then(|last| last.get("role"))
        .and_then(Json::as_str)
        == Some("tool");
    match tool {
        Some(tool) if !last_is_tool_result => {
            let arguments = json!({
                "state_change": "unchanged",
                "state_change_summary": "The stand-in reply changes no durable campaign truth.",
                "revised_player_facing_response": [{"kind": "narration", "text": TURN_REPLY}],
            });
            Reply::ToolCall { tool, arguments: arguments.to_string() }
        }
        Some(_) => Reply::Text(TURN_REPLY),
        None => Reply::Text(NARRATION),
    }
}

fn usage() -> Json {
    json!({"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40})
}

fn completion_body(reply: &Reply) -> Json {
    let (message, finish) = match reply {
        Reply::ToolCall { tool, arguments } => (
            json!({"role": "assistant", "content": null, "tool_calls": [{
                "id": "standin-call-1", "type": "function",
                "function": {"name": tool, "arguments": arguments},
            }]}),
            "tool_calls",
        ),
        Reply::Text(text) => (json!({"role": "assistant", "content": text}), "stop"),
    };
    json!({
        "id": "standin-1", "object": "chat.completion", "created": CREATED, "model": MODEL,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": usage(),
    })
}

fn stream_events(reply: &Reply) -> Vec<Json> {
    let chunk = |delta: Json, finish: Option<&str>| {
        json!({
            "id": "standin-1", "object": "chat.completion.chunk", "created": CREATED, "model": MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        })
    };
    let mut events = vec![chunk(json!({"role": "assistant", "content": ""}), None)];
    match reply {
        Reply::ToolCall { tool, arguments } => {
            events.push(chunk(
                json!({"tool_calls": [{
                    "index": 0, "id": "standin-call-1", "type": "function",
                    "function": {"name": tool, "arguments": ""},
                }]}),
                None,
            ));
            let characters: Vec<char> = arguments.chars().collect();
            for piece in characters.chunks(64) {
                let piece: String = piece.iter().collect();
                events.push(chunk(
                    json!({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]}),
                    None,
                ));
            }
            events.push(chunk(json!({}), Some("tool_calls")));
        }
        Reply::Text(text) => {
            let words: Vec<&str> = text.split(' ').collect();
            for (position, word) in words.iter().enumerate() {
                let content =
                    if position == words.len() - 1 { (*word).to_owned() } else { format!("{word} ") };
                events.push(chunk(json!({"content": content}), None));
            }
            events.push(chunk(json!({}), Some("stop")));
        }
    }
    events.push(json!({
        "id": "standin-1", "object": "chat.completion.chunk", "created": CREATED, "model": MODEL,
        "choices": [], "usage": usage(),
    }));
    events
}

type Body = std::io::Cursor<Vec<u8>>;

fn header(name: &str, value: &str) -> Header {
    Header::from_bytes(name.as_bytes(), value.as_bytes()).expect("static header is valid")
}

fn json_response(status: u16, payload: &Json) -> Response<Body> {
    Response::from_data(payload.to_string().into_bytes())
        .with_status_code(status)
        .with_header(header("Content-Type", "application/json"))
}

fn stream_response(reply: &Reply) -> Response<Body> {
    let mut body = String::new();
    for event in stream_events(reply) {
        body.push_str("data: ");
        body.push_str(&event.to_string());
        body.push_str("\n\n");
    }
    body.push_str("data: [DONE]\n\n");
    Response::from_data(body.into_bytes())
        .with_header(header("Content-Type", "text/event-stream"))
        .with_header(header("Cache-Control", "no-cache"))
        .with_header(header("Connection", "close"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn offers_a_campaign_file_call_only_before_the_tool_result() {
        let offered = json!({"tools": [{"function": {"name": "sylvia_update_campaign_file"}}],
                             "messages": [{"role": "user"}]});
        assert!(
            matches!(plan_reply(&offered), Reply::ToolCall { tool, .. } if tool == "sylvia_update_campaign_file")
        );
        let after = json!({"tools": [{"function": {"name": "update_campaign_file"}}],
                           "messages": [{"role": "tool"}]});
        assert!(matches!(plan_reply(&after), Reply::Text(TURN_REPLY)));
        assert!(matches!(plan_reply(&json!({"messages": []})), Reply::Text(NARRATION)));
    }

    #[test]
    fn stream_ends_with_usage() {
        let events = stream_events(&Reply::Text(NARRATION));
        assert_eq!(events.last().unwrap()["usage"]["total_tokens"], 40);
        let text: String =
            events.iter().filter_map(|event| event["choices"][0]["delta"]["content"].as_str()).collect();
        assert_eq!(text, NARRATION);
    }
}
