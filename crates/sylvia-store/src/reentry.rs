//! Per-task ownership of the owner's reentrant locks, as Python's per-thread `RLock`s keep it:
//! `AuthorityOwner._lane(campaign_id)` and `_narration_lock`.
//!
//! A caller that has taken an owner lane or the narration region runs its body inside
//! [`holding_lane`] or [`holding_narration`]; a nested call on the same task asks [`holds_lane`]
//! or [`holds_narration`] and, when it already holds the lock, runs without taking it again
//! instead of waiting on itself. The marks are tokio task-locals: they do not follow work into
//! `spawn_blocking` or a spawned task, so a nested call must stay on the task that took the lock.

use std::future::Future;

tokio::task_local! {
    static HELD_LANES: Vec<String>;
    static NARRATION_HELD: bool;
}

/// Whether this task holds the owner lane `key`.
pub fn holds_lane(key: &str) -> bool {
    HELD_LANES.try_with(|held| held.iter().any(|item| item == key)).unwrap_or(false)
}

/// Run `body` on this task marked as holding the owner lane `key` (the caller has taken it).
pub async fn holding_lane<F: Future>(key: &str, body: F) -> F::Output {
    let mut held = HELD_LANES.try_with(Clone::clone).unwrap_or_default();
    held.push(key.to_owned());
    HELD_LANES.scope(held, body).await
}

/// Whether this task holds the narration region.
pub fn holds_narration() -> bool {
    NARRATION_HELD.try_with(|held| *held).unwrap_or(false)
}

/// Run `body` on this task marked as holding the narration region (the caller has taken it).
pub async fn holding_narration<F: Future>(body: F) -> F::Output {
    NARRATION_HELD.scope(true, body).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_mark_holds_inside_its_body_and_its_nested_calls_only() {
        assert!(!holds_lane("campaign"));
        holding_lane("campaign", async {
            assert!(holds_lane("campaign"));
            assert!(!holds_lane("other"));
            holding_lane("other", async {
                assert!(holds_lane("campaign") && holds_lane("other"));
            })
            .await;
            assert!(!holds_lane("other"));
        })
        .await;
        assert!(!holds_lane("campaign"));
        assert!(!holds_narration());
        holding_narration(async { assert!(holds_narration()) }).await;
        assert!(!holds_narration());
    }

    #[tokio::test]
    async fn a_mark_does_not_follow_into_a_spawned_task() {
        holding_narration(async {
            assert!(!tokio::spawn(async { holds_narration() }).await.unwrap());
        })
        .await;
    }
}
