import {createStore, compose, combineReducers, applyMiddleware} from '../../../node_modules/redux/dist/redux';
import logger from '../../../node_modules/redux-logger/dist/redux-logger';

import currentActions from '../../channelstream-admin/redux/current_actions';
import serverInfoView from '../../channelstream-admin/redux/server_info';
import user from './user';
import app from './app';
import chatView from './chat_view';

const composeEnhancers = window.__REDUX_DEVTOOLS_EXTENSION_COMPOSE__ || compose;

const combinedReducers = combineReducers({app, user, currentActions, chatView, adminView: serverInfoView});

const store = createStore(combinedReducers, {}, composeEnhancers(applyMiddleware(logger)));
window.ReduxStore = store;
export {store};
