"""
This module contain functions to work with Home Assistant users.

It allows to create or delete Home Assistant user from Robonomics device list.
External functions here are manage_users() and change_password().
"""

from __future__ import annotations

import json
import logging
import typing as tp

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.providers import homeassistant as auth_ha
from homeassistant.auth import AuthProvider
from homeassistant.core import HomeAssistant


from .const import (
    CONF_SUB_OWNER_ADDRESS,
    DOMAIN,
    ROBONOMICS,
    STORE_USERS,
    TWIN_ID,
)
from .utils import (
    async_load_from_store,
    add_or_change_store,
    remove_from_store,
)

_LOGGER = logging.getLogger(__name__)


class UserManager:
    def __init__(self, hass: HomeAssistant):
        self.hass: HomeAssistant = hass
        self.provider: tp.Optional[AuthProvider] = None

    async def _set_provider(self) -> None:
        self.provider = auth_ha.async_get_provider(self.hass)
        if self.provider is None or self.provider.data is None:
            await self.provider.async_initialize()

    async def _get_hass_users(self) -> tp.List:
        if self.provider is None:
            await self._set_provider()
        return self.provider.data.users

    def _clear_users_list(self, users_list) -> tp.List:
        """Remove Controller address and Owner adress"""
        users_list_copy = users_list.copy()
        try:
            if self.hass.data[DOMAIN][ROBONOMICS].controller_address in users_list_copy:
                users_list_copy.remove(self.hass.data[DOMAIN][ROBONOMICS].controller_address)
            if self.hass.data[DOMAIN][CONF_SUB_OWNER_ADDRESS] in users_list_copy:
                users_list_copy.remove(self.hass.data[DOMAIN][CONF_SUB_OWNER_ADDRESS])
        except Exception as e:
            _LOGGER.error(
                f"Exception in deleting sub admin and sub owner from devices: {e}"
            )
        return users_list_copy

    def _get_users_to_add(self, old_users: tp.Dict, rws_devices: tp.Tuple) -> tp.List:
        users_to_add = list(set(rws_devices) - set(old_users.keys()))
        _LOGGER.debug(
            f"New users: {users_to_add}, devices: {rws_devices}, old users: {set(old_users.keys())}"
        )
        return users_to_add
    
    def _get_users_to_delete(self, old_users: tp.Dict, rws_devices: tp.Tuple) -> tp.List:
        users_to_delete = list(set(old_users.keys()) - set(rws_devices))
        _LOGGER.debug(f"Following users will be deleted: {users_to_delete}")
        return users_to_delete

    async def _create_users(self, users_list: tp.List) -> int:
        created_users = 0
        _LOGGER.debug(f"Start creating users: {users_list}")
        for user_address in users_list:
            password = await self.hass.data[DOMAIN][ROBONOMICS].find_password(
                user_address
            )
            if password != None:
                await self._delete_user_for_address_if_exists(user_address)
                await self._create_user_for_address(user_address, password)
                created_users += 1
            else:
                _LOGGER.debug(f"Password for user {user_address} wasn't found")
        return created_users

    async def _delete_users(self, users_list: tp.List) -> None:
        for user_address in users_list:
            await self._delete_user_for_address_if_exists(user_address)

    async def update_users(self, rws_devices_list: tp.Tuple[str]) -> None:
        """Compare users and data from transaction

        Compare current users of Home Assistant and Robonomics subscription device list. Decide what users must be
        created or deleted.

        :param hass: Home Assistant instance
        :param rws_devices: tuple of addresses in robonomics subscription
        """
        hass_users = await self._get_hass_users()
        _LOGGER.debug(f"Begining users: {hass_users}")
        old_users = await async_load_from_store(self.hass, STORE_USERS)
        if old_users is None:
            old_users = {}
        if rws_devices_list is None:
            rws_devices_list = []
        rws_devices = self._clear_users_list(rws_devices_list)
        users_to_add = self._get_users_to_add(old_users, rws_devices)
        users_to_delete = self._get_users_to_delete(old_users, rws_devices)
        created_users = await self._create_users(users_to_add)
        await self._delete_users(users_to_delete)

        if len(users_to_delete) > 0 or created_users > 0:
            _LOGGER.debug(f"Finishing user managment, user list: {await self._get_hass_users()}")

    async def create_or_update_user(self, data) -> None:
        """Change password for existing user or create new user

        :param hass: Home assistant instance
        :param data: data from datalog to change password

        """

        _LOGGER.debug(f"Start setting password for username {data[0]}")
        await self._set_provider()
        try:
            message = json.loads(data[2])
        except Exception as e:
            _LOGGER.warning(
                f"Message in Datalog is in wrong format: {e}\nMessage: {data[2]}"
            )
            return
        if (
            "admin" in message
            and message["subscription"] == self.hass.data[DOMAIN][CONF_SUB_OWNER_ADDRESS]
            and message["ha"] == self.hass.data[DOMAIN][ROBONOMICS].controller_address
        ):
            try:
                password = self.hass.data[DOMAIN][ROBONOMICS].decrypt_message(message["admin"], data[0])
                _LOGGER.debug(f"Decrypted password: {password}")
            except Exception as e:
                _LOGGER.error(f"Exception in change password decrypt: {e}")
                return
            try:
                await self._delete_user_for_address_if_exists(data[0])
                await self._create_user_for_address(data[0], password)

            except Exception as e:
                _LOGGER.error(f"Exception in change password: {e}")
                return
        else:
            _LOGGER.warning(
                f"Message for setting password for {data[0]} is in wrong format"
            )


    async def _create_user_for_address(
        self, address: str, password: str
    ):
        _LOGGER.debug(f"Start creating user for address {address}")
        username = await self._get_username(address)
        _LOGGER.debug(f"The username for address {address} is {username}")
        if await self._create_hass_user(username, password):
            _LOGGER.debug(f"Start saving to store user {username}")
            await add_or_change_store(
                self.hass, STORE_USERS, address, {"username": username}
            )


    async def _get_old_username(self, address: str) -> tp.Optional[str]:
        storage_data = await async_load_from_store(self.hass, STORE_USERS)
        user_info = storage_data.get(address)
        if user_info is not None:
            return user_info["username"]


    async def _delete_user_for_address_if_exists(
        self, address: str
    ):
        try:
            _LOGGER.debug(f"Start deleting user for address {address}")
            users = await self.hass.auth.async_get_users()
            old_username = await self._get_old_username(address)
            if old_username is None:
                old_username = address.lower()
            for user in users:
                if user.name == old_username:
                    await self._delete_hass_user(old_username)
            await remove_from_store(self.hass, STORE_USERS, address)
        except Exception as e:
            _LOGGER.error(f"Exception in delete user for address: {e}")


    async def _get_username(self, address: str) -> str:
        identity_name = await self.hass.data[DOMAIN][ROBONOMICS].get_identity_display_name(
            address
        )
        if identity_name is not None:
            username = identity_name
        else:
            username = address
        return username.lower()


    async def _create_hass_user(
        self, username: str, password: str
    ) -> bool:
        """Create user in Home Assistant

        :param hass: Home Assistant instance
        :param provider: Provider of user authentication
        :param username: New user username
        :param password: New user password
        """

        try:
            _LOGGER.debug(f"Start creating user: {username}")
            created_user = await self.hass.auth.async_create_user(
                username, group_ids=[GROUP_ID_USER]
            )
            await self.provider.async_add_auth(username, password)
            credentials = await self.provider.async_get_or_create_credentials(
                {"username": username}
            )
            await self.hass.auth.async_link_user(created_user, credentials)
            _LOGGER.debug(f"User was created: {username}, password: {password}")
            return True
        except Exception as e:
            _LOGGER.error(f"Exception in create user: {e}")
            return False


    async def _delete_hass_user(self, username: str) -> None:
        """Delete user from Home Assistant

        :param hass: Home Assistant instance
        :param provider: Provider of user authentication
        :param username: Username to delete
        """

        try:
            _LOGGER.debug(f"Start deleting user {username}")
            await self.provider.async_remove_auth(username)
            users = await self.hass.auth.async_get_users()
            for user in users:
                if user.name == username:
                    await self.hass.auth.async_remove_user(user)

            _LOGGER.debug(f"User was deleted: {username}")
        except Exception as e:
            _LOGGER.error(f"Exception in delete user: {e}")
