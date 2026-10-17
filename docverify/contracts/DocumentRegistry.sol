// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title DocumentRegistry
/// @notice Tamper-proof registry for document hashes (academic certificates, legal documents, etc.)
/// @dev Stores only the SHA-256 hash of a document, never the document itself.
contract DocumentRegistry {

    struct DocumentRecord {
        bytes32 docHash;        // SHA-256 hash of the document
        address issuer;         // wallet address that registered the document
        string documentType;    // e.g. "certificate", "legal_contract"
        string metadataURI;     // optional off-chain metadata (IPFS/URL) - e.g. student name, roll no
        uint256 timestamp;      // block timestamp of registration
        bool revoked;           // issuer can revoke (e.g. certificate cancelled)
    }

    address public owner;

    // hash => record
    mapping(bytes32 => DocumentRecord) private records;
    // hash => existence flag (avoids default-struct false positives)
    mapping(bytes32 => bool) private exists;
    // issuer => list of hashes they registered (for dashboards)
    mapping(address => bytes32[]) private issuerDocuments;

    // authorized issuers (institutions/organizations allowed to register documents)
    mapping(address => bool) public authorizedIssuers;

    event DocumentRegistered(bytes32 indexed docHash, address indexed issuer, string documentType, uint256 timestamp);
    event DocumentRevoked(bytes32 indexed docHash, address indexed revokedBy, uint256 timestamp);
    event IssuerAuthorized(address indexed issuer);
    event IssuerRevoked(address indexed issuer);

    modifier onlyOwner() {
        require(msg.sender == owner, "DocumentRegistry: caller is not the owner");
        _;
    }

    modifier onlyAuthorized() {
        require(authorizedIssuers[msg.sender] || msg.sender == owner, "DocumentRegistry: not an authorized issuer");
        _;
    }

    constructor() {
        owner = msg.sender;
        authorizedIssuers[msg.sender] = true;
    }

    /// @notice Register a new document hash on-chain
    function registerDocument(
        bytes32 docHash,
        string calldata documentType,
        string calldata metadataURI
    ) external onlyAuthorized {
        require(!exists[docHash], "DocumentRegistry: document already registered");

        records[docHash] = DocumentRecord({
            docHash: docHash,
            issuer: msg.sender,
            documentType: documentType,
            metadataURI: metadataURI,
            timestamp: block.timestamp,
            revoked: false
        });
        exists[docHash] = true;
        issuerDocuments[msg.sender].push(docHash);

        emit DocumentRegistered(docHash, msg.sender, documentType, block.timestamp);
    }

    /// @notice Verify whether a document hash exists and is not revoked
    function verifyDocument(bytes32 docHash)
        external
        view
        returns (
            bool isValid,
            address issuer,
            string memory documentType,
            string memory metadataURI,
            uint256 timestamp,
            bool revoked
        )
    {
        if (!exists[docHash]) {
            return (false, address(0), "", "", 0, false);
        }
        DocumentRecord memory rec = records[docHash];
        bool valid = !rec.revoked;
        return (valid, rec.issuer, rec.documentType, rec.metadataURI, rec.timestamp, rec.revoked);
    }

    /// @notice Revoke a previously registered document (only original issuer or owner)
    function revokeDocument(bytes32 docHash) external {
        require(exists[docHash], "DocumentRegistry: document not found");
        DocumentRecord storage rec = records[docHash];
        require(msg.sender == rec.issuer || msg.sender == owner, "DocumentRegistry: not authorized to revoke");
        require(!rec.revoked, "DocumentRegistry: already revoked");

        rec.revoked = true;
        emit DocumentRevoked(docHash, msg.sender, block.timestamp);
    }

    /// @notice Get all document hashes registered by a given issuer
    function getDocumentsByIssuer(address issuer) external view returns (bytes32[] memory) {
        return issuerDocuments[issuer];
    }

    /// @notice Authorize a new issuer (e.g. a university, government office)
    function authorizeIssuer(address issuer) external onlyOwner {
        authorizedIssuers[issuer] = true;
        emit IssuerAuthorized(issuer);
    }

    /// @notice Revoke an issuer's authorization
    function revokeIssuer(address issuer) external onlyOwner {
        authorizedIssuers[issuer] = false;
        emit IssuerRevoked(issuer);
    }
}
