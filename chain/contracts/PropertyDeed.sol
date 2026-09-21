// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// @notice Receiver hook required by the ERC-721 safe transfer methods.
interface IERC721Receiver {
    function onERC721Received(address operator, address from, uint256 tokenId, bytes calldata data)
        external
        returns (bytes4);
}

/**
 * @title PropertyDeed
 * @notice One non-fungible token per verified property, carrying the address of
 *         its evidence rather than the evidence itself.
 *
 * A listing on Terrax is only worth anything because of its paperwork: a title
 * deed, a tax receipt, an encumbrance certificate, and a valuation derived from
 * comparable sales. All of that lives off chain, because putting scans of a
 * private title deed on a public ledger would be both expensive and a privacy
 * failure. What goes on chain is the pair that makes the off-chain bundle
 * checkable:
 *
 *   metadataCID   where the bundle is published, as an IPFS content identifier
 *   evidenceHash  keccak256 over the document checksums and the valuation
 *
 * Anyone can fetch the CID, recompute the hash, and see whether the documents
 * they were shown are the documents that were recorded. Change one page of one
 * scan and the hashes stop matching.
 *
 * This is a deliberately small implementation of ERC-721 rather than an import
 * of OpenZeppelin, because the project carries no Node toolchain and because a
 * reader should be able to finish this file. Production would use the audited
 * library.
 */
contract PropertyDeed {
    // --- ERC-721 ----------------------------------------------------------

    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    event Approval(address indexed owner, address indexed approved, uint256 indexed tokenId);
    event ApprovalForAll(address indexed owner, address indexed operator, bool approved);

    /// @notice Emitted once per property, when its evidence is first recorded.
    event PropertyRecorded(uint256 indexed tokenId, string metadataCID, bytes32 evidenceHash);

    /// @notice Emitted when a re-valuation or a new document replaces the bundle.
    event EvidenceUpdated(uint256 indexed tokenId, string metadataCID, bytes32 evidenceHash);

    string public constant name = "Terrax Property Deed";
    string public constant symbol = "TRXD";

    address public issuer;
    uint256 public totalMinted;

    mapping(uint256 => address) private _ownerOf;
    mapping(address => uint256) private _balanceOf;
    mapping(uint256 => address) private _approved;
    mapping(address => mapping(address => bool)) private _operators;

    // --- Evidence ---------------------------------------------------------

    struct Evidence {
        string metadataCID;
        bytes32 evidenceHash;
        uint64 recordedAt;
    }

    mapping(uint256 => Evidence) public evidenceOf;

    modifier onlyIssuer() {
        require(msg.sender == issuer, "not the issuer");
        _;
    }

    constructor() {
        issuer = msg.sender;
    }

    /// @notice Hand the registry to a new operator, for a key rotation.
    function transferIssuer(address newIssuer) external onlyIssuer {
        require(newIssuer != address(0), "issuer cannot be zero");
        issuer = newIssuer;
    }

    // --- Minting ----------------------------------------------------------

    /**
     * @notice Record a property and give its deed to `to`.
     * @param to           the wallet that should hold the deed
     * @param metadataCID  IPFS identifier for the published evidence bundle
     * @param evidenceHash keccak256 over the document checksums and valuation
     * @return tokenId     sequential, starting at 1
     *
     * Only the issuer can mint. Terrax verifies documents and identity before a
     * listing is published, so an open mint would let anyone record a property
     * that nobody checked and inherit the credibility of the ones that were.
     */
    function record(address to, string calldata metadataCID, bytes32 evidenceHash)
        external
        onlyIssuer
        returns (uint256 tokenId)
    {
        require(to != address(0), "cannot mint to zero");
        require(bytes(metadataCID).length > 0, "metadata cid required");

        tokenId = ++totalMinted;
        _ownerOf[tokenId] = to;
        _balanceOf[to] += 1;
        evidenceOf[tokenId] = Evidence(metadataCID, evidenceHash, uint64(block.timestamp));

        emit Transfer(address(0), to, tokenId);
        emit PropertyRecorded(tokenId, metadataCID, evidenceHash);
    }

    /**
     * @notice Replace the evidence bundle after a re-valuation or a new document.
     *
     * The history is not overwritten: every version is an event, so the chain
     * keeps the sequence of what was claimed and when.
     */
    function updateEvidence(uint256 tokenId, string calldata metadataCID, bytes32 evidenceHash)
        external
        onlyIssuer
    {
        require(_ownerOf[tokenId] != address(0), "no such deed");
        require(bytes(metadataCID).length > 0, "metadata cid required");

        evidenceOf[tokenId] = Evidence(metadataCID, evidenceHash, uint64(block.timestamp));
        emit EvidenceUpdated(tokenId, metadataCID, evidenceHash);
    }

    /// @notice True when `evidenceHash` is what was recorded for this deed.
    function verifyEvidence(uint256 tokenId, bytes32 evidenceHash) external view returns (bool) {
        return evidenceOf[tokenId].evidenceHash == evidenceHash;
    }

    // --- Reads ------------------------------------------------------------

    function ownerOf(uint256 tokenId) public view returns (address owner) {
        owner = _ownerOf[tokenId];
        require(owner != address(0), "no such deed");
    }

    function balanceOf(address owner) external view returns (uint256) {
        require(owner != address(0), "zero address has no balance");
        return _balanceOf[owner];
    }

    function tokenURI(uint256 tokenId) external view returns (string memory) {
        require(_ownerOf[tokenId] != address(0), "no such deed");
        return string.concat("ipfs://", evidenceOf[tokenId].metadataCID);
    }

    function getApproved(uint256 tokenId) external view returns (address) {
        require(_ownerOf[tokenId] != address(0), "no such deed");
        return _approved[tokenId];
    }

    function isApprovedForAll(address owner, address operator) public view returns (bool) {
        return _operators[owner][operator];
    }

    /// @notice ERC-165: ERC-721 and ERC-165 itself.
    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == 0x80ac58cd || interfaceId == 0x01ffc9a7;
    }

    // --- Transfers --------------------------------------------------------

    function approve(address to, uint256 tokenId) external {
        address owner = ownerOf(tokenId);
        require(msg.sender == owner || isApprovedForAll(owner, msg.sender), "not authorised");

        _approved[tokenId] = to;
        emit Approval(owner, to, tokenId);
    }

    function setApprovalForAll(address operator, bool approved) external {
        _operators[msg.sender][operator] = approved;
        emit ApprovalForAll(msg.sender, operator, approved);
    }

    function transferFrom(address from, address to, uint256 tokenId) public {
        require(ownerOf(tokenId) == from, "not the holder");
        require(to != address(0), "cannot transfer to zero");
        require(
            msg.sender == from
                || msg.sender == _approved[tokenId]
                || isApprovedForAll(from, msg.sender),
            "not authorised"
        );

        delete _approved[tokenId];
        _balanceOf[from] -= 1;
        _balanceOf[to] += 1;
        _ownerOf[tokenId] = to;

        emit Transfer(from, to, tokenId);
    }

    function safeTransferFrom(address from, address to, uint256 tokenId) external {
        safeTransferFrom(from, to, tokenId, "");
    }

    function safeTransferFrom(address from, address to, uint256 tokenId, bytes memory data) public {
        transferFrom(from, to, tokenId);
        require(_accepts(from, to, tokenId, data), "receiver does not accept deeds");
    }

    /// @dev A contract that cannot answer the hook would hold the deed forever.
    function _accepts(address from, address to, uint256 tokenId, bytes memory data)
        private
        returns (bool)
    {
        if (to.code.length == 0) {
            return true;
        }
        try IERC721Receiver(to).onERC721Received(msg.sender, from, tokenId, data) returns (
            bytes4 answer
        ) {
            return answer == IERC721Receiver.onERC721Received.selector;
        } catch {
            return false;
        }
    }
}
