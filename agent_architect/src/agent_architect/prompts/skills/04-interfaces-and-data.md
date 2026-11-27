Use this to define how components talk and what data they store.

- Interfaces: for each new or changed API give method, path, request and
  response shapes, status codes and error cases; for events or messages give
  name, producer, consumers and payload. Follow the existing API style.
- Authentication and authorisation: who may call each interface and how this
  is enforced.
- Data model: new or changed entities with their key fields, types,
  relationships, uniqueness and indexes. State where each entity is stored.
- Migrations: schema changes and how existing data is handled.
- Keep sensitive data (passwords, tokens, personal data) protected: say how it
  is encrypted, masked or kept out of logs.
