# Iterative Cross-Referencing (ICR)

*A Pre-Model Context Resolution Pattern*

February 2026

---

## The Problem

Large language models are general-purpose reasoners. When given a question and the relevant context, they perform well. When given a question and told to find the relevant context themselves, they perform worse — not because they lack reasoning ability, but because search is a different skill than execution. Every tool call, every retrieval step, every reasoning loop spent figuring out where data lives is time and capacity not spent on the actual task.

The standard approach is to make models better at searching. Retrieval-augmented generation, tool use, multi-step planning, chain-of-thought reasoning. All of these assume the model should be the one figuring out where the answer lives.

There is another option: resolve the "where" before the model is ever involved.

---

## Overview

A resolution layer sits between the user and the model. When a user sends a message, the layer checks it against a pre-built index of the data the user is working with. If the message references something in the index, the layer attaches the exact location of the relevant data to the message before the model sees it. If nothing matches, the message passes through unchanged.

The model receives one of two things: a message with the answer to "where is the data I need" already attached, or the original message with nothing changed. It does not know which happened. It does not need to.

A library's catalog system does not understand its books. The Dewey Decimal System does not read them. But if someone walks in and mentions a topic, the catalog maps that topic to a shelf number, and the librarian walks straight there without deliberation. ICR is the process of building that catalog by examining what is already on the shelves. Resolution is the lookup. The model is the librarian who receives the card with the shelf number already written on it — before it knew someone walked in.

---

## Building the Index

The index that makes resolution possible has to come from somewhere. In domains with schemas, documentation, or APIs, building it is straightforward. The interesting case is when none of that exists — when the data is undocumented, unstructured, or binary.

ICR is a method for building the index from the data itself. It works as a single continuous loop — a web crawl over the data's own structure.

### Seed

ICR begins with whatever the data already contains that is human-readable. In a codebase, this might be function names and comments. In a database, column headers and enum values. In a binary format, decoded text or string tables. Containers whose record count matches a known table's entry count are queued first — they are the most likely starting points.

### Loop

Each queued container is visited once. On visit, every record is scanned: every value at every offset is checked against every known table. If a value at a particular offset consistently maps to valid entries across the majority of records, that offset is a confirmed field. The container is labeled immediately from those confirmed fields — labeling is not deferred.

Then edges are followed. Any unvisited container that shares a table reference with the one just scanned gets queued next. The graph reveals itself as it is walked. Containers that were unreachable from the seed become reachable because something already visited pointed to them.

The visited set is the only filter. A container is scanned once and never again.

### Termination

The loop ends when the queue is empty — when there are no more unvisited containers reachable from any confirmed connection. Containers with no shared references to known tables are never queued and never visited. Graphics, sound, and other non-relational data fall out naturally because nothing points to them.

The result is a flat index. Every labeled record has a path (where it lives) and a description (what it is). Both were derived entirely from the data. Nothing was hardcoded. No schema was provided. The data named itself.

---

## Resolution

At query time, the user's message is checked against the ICR-built index using string matching. If the message contains terms that appear in index labels, the corresponding paths are pulled and attached to the message as a routing header.

Resolution involves no inference, no embeddings, no vector search, and no model. It is string matching against an in-memory table. It completes in under 100 milliseconds.

If no match is found, the resolution layer steps aside entirely. The original message passes through to the model unchanged. The model operates normally, using whatever tools and reasoning it has available. ICR's resolution is additive. When it has nothing to contribute, it is invisible.

---

## Placement

The resolution layer's position matters. It sits before the model, not beside it. It is not a tool the model calls. It is not a retrieval system the model queries. It operates on the message before the model receives it.

This means the model never decides whether to use it. The model never waits for it. The model never reasons about whether context is needed. If context was available, it is already there. If it was not, nothing changed.

From the model's perspective, the routing context is part of the message. It acts on it the same way it would act on any other information the user provided. The distinction between "the user told me where the data is" and "a resolution layer told me where the data is" does not exist from the model's side.

From the user's perspective, they asked a question and got an answer. They did not provide a path. They did not specify which data source to use. They did not invoke a tool. The resolution was invisible.

Neither side knows the other's version of the message. Two timelines.

---

## What ICR Replaces

In a standard tool-use workflow, the sequence for answering a question about data is: the user asks, the model reasons about where to look, the model calls a search or retrieval tool, the tool returns results, the model evaluates results, potentially searches again, and then acts on what it found. This can take multiple round trips and consumes context window and reasoning capacity on navigation rather than execution.

With ICR, the sequence is: the user asks, the resolution layer attaches the relevant path, the model reads the data at that path and acts. One step of navigation, handled outside the model, replaced multiple steps of reasoning handled inside it.

ICR is not retrieval-augmented generation. RAG fetches content and injects it into context. ICR resolves a path and attaches it as a routing header. The model still reads the data itself using its existing tools — but it never has to search for it.

ICR is not a query planner. A query planner decides how to decompose a request into sub-queries. ICR resolves what the user is referencing and where it lives. There is no decomposition. There is no planning. There is a lookup.

The model's reasoning capacity is fully available for the actual task because none of it was spent on finding the data.

---

## Properties

**Self-describing.** The index is derived from the data's own contents. No external schema, documentation, or manual annotation is required. If the data contains values that reference known entities, those references are the index.

**Pre-model.** Resolution happens before the model is involved. The model's reasoning capacity is not consumed by navigation.

**Additive.** When the resolution layer has nothing to contribute, the message passes through unchanged. The model's normal capabilities are never reduced.

**Sub-inference.** Resolution is string matching, not inference. It does not require a GPU, a vector database, or an embedding model. It runs in constant time relative to the index size.

**Invisible.** Neither the user nor the model is aware that resolution occurred. The user sees their original message. The model sees a message with context attached. Both operate as if the context was always there.
