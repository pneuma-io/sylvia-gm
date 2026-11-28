//! The action sequence entry points over the authority's [`AuthoritySequences`] can be boxed as
//! `Send` futures, as every `TurnOwner` method and owner command boxes its work.
//!
//! Awaiting them directly in a test never checks this: the `Send` bound on a future that borrows
//! a lifetime-generic owner is a higher-ranked check (rust-lang/rust#100013), and it failed
//! through `SequenceOwner`'s generic guard closures. These functions only have to compile.

use std::future::Future;
use std::pin::Pin;
use sylvia_app::action_sequences::{
    action_sequence, compose_action_sequence, execute_action_sequence, ComposeRequest, ExecuteRequest,
    SequenceResult,
};
use sylvia_app::authority::sequence_owner::AuthoritySequences;
use sylvia_base::JsonMap;
use sylvia_store::StoreHandle;

type SendFuture<'a, T> = Pin<Box<dyn Future<Output = SequenceResult<T>> + Send + 'a>>;

#[allow(dead_code)]
fn read_is_send<'a>(store: &'a StoreHandle, session: &'a str) -> SendFuture<'a, Option<JsonMap>> {
    Box::pin(async move { action_sequence(&AuthoritySequences::new(store, "owner"), session, None).await })
}

#[allow(dead_code)]
fn compose_is_send<'a>(store: &'a StoreHandle, request: &'a ComposeRequest) -> SendFuture<'a, JsonMap> {
    Box::pin(
        async move { compose_action_sequence(&AuthoritySequences::new(store, "owner"), request, true).await },
    )
}

#[allow(dead_code)]
fn execute_is_send<'a>(store: &'a StoreHandle, request: &'a ExecuteRequest) -> SendFuture<'a, JsonMap> {
    Box::pin(
        async move { execute_action_sequence(&AuthoritySequences::new(store, "owner"), request, true).await },
    )
}

#[test]
fn the_three_entry_points_box_as_send_futures() {
    // Compiling this file is the check; naming the functions keeps them in the build.
    let _ =
        (read_is_send as fn(_, _) -> _, compose_is_send as fn(_, _) -> _, execute_is_send as fn(_, _) -> _);
}
