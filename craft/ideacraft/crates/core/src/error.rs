/// Everything that can go wrong in the model.
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum Error {
    #[error("no node {0}")]
    NoNode(u64),
    #[error("no link {0}")]
    NoEdge(u64),
    #[error("a node can't be linked to itself")]
    SelfLink,
    #[error("that parent would make a cycle")]
    Cycle,
    #[error("the map is full ({0})")]
    Full(&'static str),
    #[error("invalid colour {0:?}: use #rrggbb")]
    Color(String),
    #[error("invalid file: {0}")]
    Format(String),
}

pub type Result<T> = std::result::Result<T, Error>;
