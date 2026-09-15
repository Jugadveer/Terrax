// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// @notice Receiver hooks required by the ERC-1155 transfer methods.
interface IERC1155Receiver {
    function onERC1155Received(
        address operator,
        address from,
        uint256 id,
        uint256 value,
        bytes calldata data
    ) external returns (bytes4);

    function onERC1155BatchReceived(
        address operator,
        address from,
        uint256[] calldata ids,
        uint256[] calldata values,
        bytes calldata data
    ) external returns (bytes4);
}

/**
 * @title PropertyShares
 * @notice Fractional ownership of a recorded property, as ERC-1155 balances.
 *
 * The token id is the deed's token id, so a share and the property it is a
 * share of are joined by the number rather than by a lookup table that can
 * drift. One contract holds every property's shares, which is the reason to
 * choose ERC-1155 over a separate ERC-20 per property: the same pool
 * accounting, one deployment, one approval.
 *
 * This mirrors the off-chain ledger exactly. `issue` is the primary sale that
 * `market.services.buy_shares` performs, `redeem` is the buy-back that
 * `sell_shares` performs, and `unsold` is the same number the listing page
 * shows as shares still available. The invariant the Python tests assert --
 * that issued shares never exceed the supply and that every share is held by
 * somebody -- is enforced here by the contract rather than by convention.
 */
contract PropertyShares {
    // --- ERC-1155 ---------------------------------------------------------

    event TransferSingle(
        address indexed operator, address indexed from, address indexed to, uint256 id, uint256 value
    );
    event TransferBatch(
        address indexed operator,
        address indexed from,
        address indexed to,
        uint256[] ids,
        uint256[] values
    );
    event ApprovalForAll(address indexed owner, address indexed operator, bool approved);
    event URI(string value, uint256 indexed id);

    /// @notice Emitted when a property is first opened for fractional sale.
    event PoolOpened(uint256 indexed deedId, uint256 supply);

    string public constant name = "Basix Property Shares";
    string public constant symbol = "BSXS";

    address public issuer;

    /// @notice Total shares that exist for a deed. Zero means never opened.
    mapping(uint256 => uint256) public supplyOf;

    /// @notice Shares of a deed currently held by someone.
    mapping(uint256 => uint256) public issuedOf;

    mapping(uint256 => mapping(address => uint256)) private _balances;
    mapping(address => mapping(address => bool)) private _operators;

    modifier onlyIssuer() {
        require(msg.sender == issuer, "not the issuer");
        _;
    }

    constructor() {
        issuer = msg.sender;
    }

    function transferIssuer(address newIssuer) external onlyIssuer {
        require(newIssuer != address(0), "issuer cannot be zero");
        issuer = newIssuer;
    }

    // --- The pool ---------------------------------------------------------

    /**
     * @notice Open a property for fractional sale with a fixed supply.
     *
     * The supply is set once. Raising it later would dilute everyone who
     * already bought in, and doing that silently is the failure mode this
     * whole project exists to argue against.
     */
    function open(uint256 deedId, uint256 supply) external onlyIssuer {
        require(deedId != 0, "deed id required");
        require(supply > 0, "supply required");
        require(supplyOf[deedId] == 0, "already open");

        supplyOf[deedId] = supply;
        emit PoolOpened(deedId, supply);
    }

    /// @notice Shares of a deed still sitting in the pool.
    function unsold(uint256 deedId) public view returns (uint256) {
        return supplyOf[deedId] - issuedOf[deedId];
    }

    /// @notice Primary sale: move shares out of the pool to a buyer.
    function issue(uint256 deedId, address to, uint256 amount) external onlyIssuer {
        require(to != address(0), "cannot issue to zero");
        require(amount > 0, "amount required");
        require(amount <= unsold(deedId), "not enough shares left");

        issuedOf[deedId] += amount;
        _balances[deedId][to] += amount;

        emit TransferSingle(msg.sender, address(0), to, deedId, amount);
        require(_accepts(address(0), to, deedId, amount, ""), "receiver does not accept shares");
    }

    /// @notice Exit: return a holder's shares to the pool.
    function redeem(uint256 deedId, address from, uint256 amount) external onlyIssuer {
        require(amount > 0, "amount required");
        require(_balances[deedId][from] >= amount, "not enough shares held");

        _balances[deedId][from] -= amount;
        issuedOf[deedId] -= amount;

        emit TransferSingle(msg.sender, from, address(0), deedId, amount);
    }

    // --- Reads ------------------------------------------------------------

    function balanceOf(address owner, uint256 id) public view returns (uint256) {
        require(owner != address(0), "zero address has no balance");
        return _balances[id][owner];
    }

    function balanceOfBatch(address[] calldata owners, uint256[] calldata ids)
        external
        view
        returns (uint256[] memory balances)
    {
        require(owners.length == ids.length, "length mismatch");

        balances = new uint256[](owners.length);
        for (uint256 i = 0; i < owners.length; i++) {
            balances[i] = balanceOf(owners[i], ids[i]);
        }
    }

    function isApprovedForAll(address owner, address operator) public view returns (bool) {
        return _operators[owner][operator];
    }

    /// @notice ERC-165: ERC-1155 and ERC-165 itself.
    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == 0xd9b67a26 || interfaceId == 0x01ffc9a7;
    }

    // --- Transfers --------------------------------------------------------

    function setApprovalForAll(address operator, bool approved) external {
        _operators[msg.sender][operator] = approved;
        emit ApprovalForAll(msg.sender, operator, approved);
    }

    function safeTransferFrom(address from, address to, uint256 id, uint256 value, bytes calldata data)
        external
    {
        require(to != address(0), "cannot transfer to zero");
        require(from == msg.sender || isApprovedForAll(from, msg.sender), "not authorised");
        require(_balances[id][from] >= value, "not enough shares held");

        _balances[id][from] -= value;
        _balances[id][to] += value;

        emit TransferSingle(msg.sender, from, to, id, value);
        require(_accepts(from, to, id, value, data), "receiver does not accept shares");
    }

    function safeBatchTransferFrom(
        address from,
        address to,
        uint256[] calldata ids,
        uint256[] calldata values,
        bytes calldata data
    ) external {
        require(to != address(0), "cannot transfer to zero");
        require(ids.length == values.length, "length mismatch");
        require(from == msg.sender || isApprovedForAll(from, msg.sender), "not authorised");

        for (uint256 i = 0; i < ids.length; i++) {
            require(_balances[ids[i]][from] >= values[i], "not enough shares held");
            _balances[ids[i]][from] -= values[i];
            _balances[ids[i]][to] += values[i];
        }

        emit TransferBatch(msg.sender, from, to, ids, values);
        require(_acceptsBatch(from, to, ids, values, data), "receiver does not accept shares");
    }

    // --- Receiver hooks ---------------------------------------------------

    function _accepts(address from, address to, uint256 id, uint256 value, bytes memory data)
        private
        returns (bool)
    {
        if (to.code.length == 0) {
            return true;
        }
        try IERC1155Receiver(to).onERC1155Received(msg.sender, from, id, value, data) returns (
            bytes4 answer
        ) {
            return answer == IERC1155Receiver.onERC1155Received.selector;
        } catch {
            return false;
        }
    }

    function _acceptsBatch(
        address from,
        address to,
        uint256[] calldata ids,
        uint256[] calldata values,
        bytes memory data
    ) private returns (bool) {
        if (to.code.length == 0) {
            return true;
        }
        try IERC1155Receiver(to).onERC1155BatchReceived(msg.sender, from, ids, values, data) returns (
            bytes4 answer
        ) {
            return answer == IERC1155Receiver.onERC1155BatchReceived.selector;
        } catch {
            return false;
        }
    }
}
